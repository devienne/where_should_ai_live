-- =====================================================================
-- Where should AI live? — SQL showcase queries (PostgreSQL + PostGIS)
--
-- Each query starts with a "-- name: <id>" line. The notebook
-- (notebooks/02_sql_showcase.ipynb) splits this file on those markers and
-- runs each query by name, so the SQL lives here once and isn't copied around.
-- No psql-only commands (\x, \echo, ...), so it runs from Python as well.
-- =====================================================================


-- name: cost_by_zone
-- What does a square metre cost in each zone? Median + interquartile range
-- (medians, because a few luxury listings would drag an average up).
SELECT
    zone,
    COUNT(*)                                                                      AS n_listings,
    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY cost_per_m2)::numeric, 1)  AS p25_cost_m2,
    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY cost_per_m2)::numeric, 1)  AS median_cost_m2,
    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY cost_per_m2)::numeric, 1)  AS p75_cost_m2,
    ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY total_cost)::numeric, 0)   AS median_total_cost
FROM listings
GROUP BY zone
ORDER BY median_cost_m2 DESC;


-- name: best_deals_by_bairro
-- The cheapest listings per m² *relative to their own bairro*.
-- Comparing within a bairro removes the location effect: a R$ 60/m² flat is
-- cheap in Pinheiros but expensive in Itaquera.
--   RANK          -> 1 = cheapest per m² in the bairro
--   PERCENT_RANK  -> 0.0 = cheapest, 1.0 = most expensive
WITH ranked AS (
    SELECT
        listing_id, bairro, zone, property_type, area_m2, bedrooms, total_cost, cost_per_m2,
        RANK()         OVER w AS rank_in_bairro,
        PERCENT_RANK() OVER w AS pct_rank_in_bairro,
        COUNT(*)       OVER (PARTITION BY bairro) AS bairro_listings
    FROM listings
    WHERE property_type = 'apartamento'          -- compare like with like
    WINDOW w AS (PARTITION BY bairro ORDER BY cost_per_m2)
)
SELECT
    bairro, zone, rank_in_bairro, bairro_listings,
    ROUND(pct_rank_in_bairro::numeric, 2) AS pct_rank_in_bairro,
    listing_id, area_m2, bedrooms, total_cost, cost_per_m2
FROM ranked
WHERE rank_in_bairro <= 3
ORDER BY zone, bairro, rank_in_bairro;


-- name: station_premium
-- Do listings near a metro/train station cost more per m²?
-- "Near" = within 500 m (a ~6-minute walk) of any station.
-- Needs the `stations` table (Day 7, task 2).
WITH flagged AS (
    SELECT
        l.zone,
        l.cost_per_m2,
        EXISTS (                                 -- stops at the first station found
            SELECT 1 FROM stations s
            WHERE ST_DWithin(l.geog, s.geog, 500)
        ) AS near_station
    FROM listings l
)
SELECT
    zone,
    COUNT(*) FILTER (WHERE near_station)                                          AS n_near,
    COUNT(*) FILTER (WHERE NOT near_station)                                      AS n_far,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cost_per_m2)
          FILTER (WHERE near_station)::numeric, 1)                                AS median_cost_m2_near,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cost_per_m2)
          FILTER (WHERE NOT near_station)::numeric, 1)                            AS median_cost_m2_far
FROM flagged
GROUP BY zone
ORDER BY zone;


-- name: station_premium_within_bairro
-- Same question, but comparing near vs far *inside each bairro*, so the
-- bairro's other qualities (prestige, safety, size of homes) are held constant.
-- Only bairros with >= 5 listings on each side, so both medians mean something.
WITH flagged AS (
    SELECT
        l.bairro, l.zone, l.cost_per_m2,
        EXISTS (SELECT 1 FROM stations s WHERE ST_DWithin(l.geog, s.geog, 500)) AS near_station
    FROM listings l
), per_bairro AS (
    SELECT
        bairro, zone,
        COUNT(*) FILTER (WHERE near_station)                                                   AS n_near,
        COUNT(*) FILTER (WHERE NOT near_station)                                               AS n_far,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cost_per_m2) FILTER (WHERE near_station)     AS median_near,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cost_per_m2) FILTER (WHERE NOT near_station) AS median_far
    FROM flagged
    GROUP BY bairro, zone
)
SELECT
    bairro, zone, n_near, n_far,
    ROUND(median_near::numeric, 1)                         AS median_cost_m2_near,
    ROUND(median_far::numeric, 1)                          AS median_cost_m2_far,
    ROUND((100 * (median_near / median_far - 1))::numeric, 1) AS premium_pct
FROM per_bairro
WHERE n_near >= 5 AND n_far >= 5
ORDER BY premium_pct DESC;


-- name: nearest_station_distance
-- Distance from every listing to its nearest station, next to QuintoAndar's flag.
-- LATERAL runs the inner query once per listing; ORDER BY <-> ... LIMIT 1 is an
-- index-assisted nearest-neighbour search. (No listing ids: aggregate use only.)
SELECT
    l.metro_near,
    nearest.kind     AS nearest_station_kind,
    nearest.metres   AS metres_to_station
FROM listings l
CROSS JOIN LATERAL (
    SELECT s.kind, ST_Distance(l.geog, s.geog) AS metres
    FROM stations s
    ORDER BY l.geog <-> s.geog
    LIMIT 1
) AS nearest;


-- name: metro_flag_check
-- How reliable is QuintoAndar's "metrô próximo" flag? Compare it with the real
-- distance to the nearest station (same LATERAL nearest-neighbour search as above).
WITH nearest AS (
    SELECT
        l.metro_near,
        n.metres AS metres_to_station
    FROM listings l
    CROSS JOIN LATERAL (
        SELECT ST_Distance(l.geog, s.geog) AS metres
        FROM stations s
        ORDER BY l.geog <-> s.geog
        LIMIT 1
    ) AS n
)
SELECT
    metro_near                                                                          AS quintoandar_says_near,
    COUNT(*)                                                                            AS n_listings,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY metres_to_station)::numeric, 0)   AS median_metres_to_station,
    ROUND(100.0 * AVG((metres_to_station <= 500)::int), 1)                              AS pct_really_within_500m,
    ROUND(100.0 * AVG((metres_to_station <= 1000)::int), 1)                             AS pct_within_1km
FROM nearest
GROUP BY metro_near
ORDER BY metro_near DESC;


-- name: listings_per_h3_cell
-- Listings, price and bairros per H3 cell (resolution 8, ~0.7 km²).
-- Only cells with >= 5 listings: a median of 1-2 values isn't meaningful.
SELECT
    h3_r8,
    COUNT(*)                                                                      AS n_listings,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cost_per_m2)::numeric, 1)   AS median_cost_m2,
    MIN(zone)                                                                     AS zone,      -- a cell may straddle zones; pick one for colouring
    STRING_AGG(DISTINCT bairro, ', ' ORDER BY bairro)                             AS bairros
FROM listings
GROUP BY h3_r8
HAVING COUNT(*) >= 5
ORDER BY n_listings DESC;


-- name: h3_cell_size_distribution
-- How evenly are listings spread over cells? (how many cells hold 1, 2-4, 5-9, ... listings)
WITH per_cell AS (
    SELECT h3_r8, COUNT(*) AS n FROM listings GROUP BY h3_r8
)
SELECT
    CASE
        WHEN n = 1  THEN '1'
        WHEN n < 5  THEN '2-4'
        WHEN n < 10 THEN '5-9'
        WHEN n < 20 THEN '10-19'
        ELSE '20+'
    END              AS listings_in_cell,
    COUNT(*)         AS n_cells,
    SUM(n)           AS n_listings
FROM per_cell
GROUP BY 1
ORDER BY MIN(n);
