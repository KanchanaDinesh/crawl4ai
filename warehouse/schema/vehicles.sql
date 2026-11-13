-- vehicles: vehicle ads over time (vehicles/). Moved from data/vehicles/vehicles.duckdb on 2026-11-06.

create table if not exists vehicles.ads (
    saved_at   timestamptz,
    site       text,
    rank       integer,
    title      text,
    url        text,
    price      text,
    price_lkr  bigint,
    location   text,
    year       text,
    mileage    text,
    posted     text,
    details    text,
    image      text,
    extra      jsonb
);
create index if not exists ads_site_saved_at_idx on vehicles.ads (site, saved_at);
create index if not exists ads_url_idx on vehicles.ads (url);

create table if not exists vehicles.runs (
    saved_at    timestamptz,
    site        text,
    source_url  text,
    success     boolean,
    ads         integer,
    seconds     double precision,
    error       text
);
create index if not exists runs_saved_at_idx on vehicles.runs (saved_at);

-- category: what the listing sells, "car" or "motorcycle" (vehicles/config/sites.json; added 2026-11-13)
alter table vehicles.ads add column if not exists category text;
alter table vehicles.runs add column if not exists category text;
create index if not exists ads_category_idx on vehicles.ads (category, saved_at);

-- Ads saved before categories: riyasewana only ever read cars (and SUVs once). ikman read its mixed "vehicles" page
-- until 2026-11-12, but each ad's location ends with ikman's own category ("Colombo, Motorbikes"), so that says which.
-- Only rows with no category are touched, so this is safe to repeat.
update vehicles.ads set category = case
        when site <> 'ikman' then 'car'
        when location like '%, Cars' then 'car'
        when location like '%, Motorbikes' then 'motorcycle'
        when location like '%, Three Wheelers' then 'three-wheeler'
        when location like '%, Vans' then 'van'
        when location like '%, Lorries & Trucks' then 'lorry'
        when location like '%, Buses' then 'bus'
        when location like '%, Bicycles' then 'bicycle'
        else 'other' end                    -- parts, services, rentals
    where category is null;
update vehicles.runs set category = 'car'
    where category is null and (site <> 'ikman' or source_url like '%/cars');

create table if not exists vehicles.meta (
    key    text primary key,
    value  text
);

-- every ad seen, per run
create or replace view vehicles.v_vehicle_ads as
    select saved_at, site, title, url, price, price_lkr, location, year, mileage, posted, image, rank, details, category
    from vehicles.ads;

-- one row per ad: its latest details, when it was first seen and how many runs saw it
create or replace view vehicles.v_vehicle_ads_latest as
    select saved_at, site, title, url, price, price_lkr, location, year, mileage, posted, image, rank, details,
           first_seen, times_seen, category
    from (select *, min(saved_at) over (partition by url) as first_seen,
                 count(*) over (partition by url) as times_seen,
                 row_number() over (partition by url order by saved_at desc) as rn
          from vehicles.v_vehicle_ads) a
    where rn = 1
      and not exists (select 1 from audit.hidden_records h where h.dataset = 'vehicles.ads' and h.record = a.url);

-- ads whose price changed between two runs
create or replace view vehicles.v_vehicle_price_changes as
    select url, site, title, saved_at, price_lkr, previous_price_lkr, category
    from (select url, site, title, saved_at, price_lkr, category,
                 lag(price_lkr) over (partition by url order by saved_at) as previous_price_lkr
          from vehicles.v_vehicle_ads) a
    where previous_price_lkr is not null and previous_price_lkr <> price_lkr
      and not exists (select 1 from audit.hidden_records h where h.dataset = 'vehicles.ads' and h.record = a.url);
