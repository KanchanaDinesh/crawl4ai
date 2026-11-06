"""The AI assistant's page: ask, see the answer and its query, save it. Each question is logged, with what it cost, and limited per day."""

import os
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from webapp.accounts.rights import ADMINISTRATOR, menu_required
from webapp.audit.log import record
from webapp.catalog import rights
from webapp.catalog.models import Dataset

from . import agent, prompt, running, saving, sqlcheck
from .models import Question

MAX_QUESTION = 1000
DAILY_LIMIT = 20
MONTHLY_BUDGET_USD = 50


def daily_limit() -> int:
    try:
        return int(os.environ.get("ASSISTANT_DAILY_LIMIT") or DAILY_LIMIT)
    except ValueError:
        return DAILY_LIMIT


def monthly_budget() -> Decimal:
    try:
        return Decimal(os.environ.get("ASSISTANT_MONTHLY_USD") or MONTHLY_BUDGET_USD)
    except ArithmeticError:
        return Decimal(MONTHLY_BUDGET_USD)


def _start_of_day():
    return timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)


def _asked_today(user) -> int:
    return Question.objects.filter(user=user, at__gte=_start_of_day()).count()


def _spent_this_month() -> Decimal:
    start = _start_of_day().replace(day=1)
    return Question.objects.filter(at__gte=start).aggregate(total=Sum("cost_usd"))["total"] or Decimal(0)


def _limit_problem(user) -> str:
    limit = daily_limit()
    if _asked_today(user) >= limit:
        return f"That's your {limit} questions for today. More tomorrow; an administrator can raise the limit."
    if _spent_this_month() >= monthly_budget():
        return f"The assistant has used this month's budget of ${monthly_budget()}. An administrator can raise it."
    return ""


def _counts(user) -> dict:
    return {"recent": Question.objects.filter(user=user)[:15], "left": max(0, daily_limit() - _asked_today(user)), "limit": daily_limit()}


def _home_context(request) -> dict:
    saved = rights.visible_datasets(request.user).filter(schema="userdata", name__startswith="a_").order_by("label")
    return {**_counts(request.user), "saved": saved, "configured": agent.is_configured(), "is_admin": rights.is_admin(request.user), "max_question": MAX_QUESTION}


@menu_required("assistant")
def home(request):
    return render(request, "assistant/home.html", _home_context(request))


def _store(question: Question, a: agent.Answer) -> None:
    question.status, question.title, question.explanation = a.status, a.title, a.explanation
    question.clarification, question.problem, question.sql, question.sources = a.clarification, a.problem, a.sql, a.sources
    question.rows, question.calls, question.model = a.total, a.usage.calls, a.model
    question.input_tokens, question.output_tokens = a.usage.input_tokens, a.usage.output_tokens
    question.cache_read_tokens, question.cache_write_tokens = a.usage.cache_read_tokens, a.usage.cache_write_tokens
    question.cost_usd, question.seconds = a.usage.cost(a.model), a.seconds
    question.save()


@require_POST
@menu_required("assistant")
def ask(request):
    text = " ".join(request.POST.get("question", "").split())
    if not text:
        return render(request, "assistant/_answer.html", {"problem": "Type a question first."})
    if len(text) > MAX_QUESTION:
        return render(request, "assistant/_answer.html", {"problem": f"That's over {MAX_QUESTION:,} characters. Ask for something shorter."})
    problem = _limit_problem(request.user)
    if problem:
        return render(request, "assistant/_answer.html", {"problem": problem})
    question = Question(user=request.user, text=text)
    a = agent.ask(text, request.user, timezone.localdate().isoformat())
    _store(question, a)
    record(request, "ai_question", f"Asked: {text[:200]}", target_type="ai_question", target_id=question.pk,
           detail={"status": a.status, "sql": a.sql[:2000], "rows": a.total, "calls": a.usage.calls, "cost_usd": str(question.cost_usd), "sources": a.sources})
    # the count and the list on the page move on with the answer
    return render(request, "assistant/_answer.html", {**_answer_context(question, a.result), **_counts(request.user), "swap": True})


def _answer_context(question: Question, result) -> dict:
    return {"q": question, "result": result, "problem": question.problem, "preview_limit": running.PREVIEW_ROWS}


def _own_question(request, pk) -> Question:
    question = get_object_or_404(Question, pk=pk)
    if question.user_id != request.user.pk and not rights.is_admin(request.user):
        raise PermissionDenied("That question was someone else's.")
    return question


@menu_required("assistant")
def question_page(request, pk):
    """An earlier answer, worked out again: with the data as it is now, and only if the person may still see what it reads."""
    question = _own_question(request, pk)
    result, problem = None, question.problem
    if question.sql:
        try:
            checked = sqlcheck.check(question.sql, prompt.allowed_map(prompt.usable_datasets(request.user)))
            result = running.run(checked.sql)
            problem = ""
        except (sqlcheck.SqlRefused, running.QueryFailed, RuntimeError) as e:
            problem = str(e)
    return render(request, "assistant/question.html", {"q": question, "result": result, "problem": problem, "preview_limit": running.PREVIEW_ROWS})


@require_POST
@menu_required("assistant")
def save(request, pk):
    question = _own_question(request, pk)
    if not question.sql:
        messages.error(request, "That question has no query to save.")
        return redirect("assistant_question", question.pk)
    try:
        ds = saving.save(question, request.POST.get("label", ""), request.user)
    except saving.SaveProblem as e:
        messages.error(request, str(e))
        return redirect("assistant_question", question.pk)
    record(request, "ai_saved", f"AI answer saved as “{ds.label}”", target_type="dataset", target_id=ds.ref, detail={"question": question.pk, "sql": question.sql[:2000], "sources": question.sources})
    messages.success(request, f"Saved “{ds.label}”. It's a live dataset: it follows the data from now on.")
    return redirect("dataset", ds.ref)


@menu_required("assistant")
def saved_page(request, pk):
    ds = get_object_or_404(Dataset, pk=pk, schema="userdata", name__startswith="a_", missing=False)
    if not rights.can_view(request.user, ds):
        raise PermissionDenied("That reads data your roles don't include.")
    from_question = ds.questions.first()
    context = {"ds": ds, "question": from_question, "sources": ds.query_sources.all(), "may_share": rights.can_share(request.user, ds)}
    if context["may_share"]:
        current = {r.group_id: r for r in ds.rights.all()}
        context["roles"] = [{"group": g, "right": current.get(g.pk)} for g in Group.objects.exclude(name=ADMINISTRATOR).order_by("name")]
    return render(request, "assistant/saved.html", context)


@menu_required("assistant")
def usage(request):
    """What the assistant is used for and costs, for the first weeks especially."""
    if not rights.is_admin(request.user):
        raise PermissionDenied("Only administrators can see this.")
    since = timezone.now() - timedelta(days=30)
    recent = Question.objects.filter(at__gte=since)
    by_user = recent.values("user__username").annotate(questions=Count("id"), cost=Sum("cost_usd"), tokens_in=Sum("input_tokens"), tokens_out=Sum("output_tokens")).order_by("-cost")
    by_status = recent.values("status").annotate(n=Count("id")).order_by("-n")
    return render(request, "assistant/usage.html", {"by_user": by_user, "by_status": by_status, "total": recent.aggregate(total=Sum("cost_usd"))["total"] or 0, "spent": _spent_this_month(), "budget": monthly_budget(),
                                                    "limit": daily_limit(), "questions": Question.objects.select_related("user")[:100]})
