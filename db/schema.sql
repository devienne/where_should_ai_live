-- =====================================================================
-- Where should AI live? — database schema (PostgreSQL 16 + PostGIS 3.4)
-- Applied by: python -m db.init_db
-- Safe to run more than once (IF NOT EXISTS everywhere).
-- =====================================================================

-- PostGIS adds spatial types (geography/geometry), functions (ST_DWithin, ...) and GiST indexes
CREATE EXTENSION IF NOT EXISTS postgis;


-- ---------------------------------------------------------------------
-- listings: one row per rental listing (from data/processed/listings_clean.csv)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS listings (
    listing_id      TEXT PRIMARY KEY,               -- QuintoAndar id from the URL (key for upserts)
    url             TEXT NOT NULL UNIQUE,
    bairro          TEXT NOT NULL,                  -- QuintoAndar slug, e.g. 'vila-mariana'
    zone            TEXT NOT NULL
                    CHECK (zone IN ('Centro', 'Oeste', 'Sul', 'Norte', 'Leste')),
    scraped_at      TIMESTAMP NOT NULL,
    property_type   TEXT,                           -- apartamento / casa / kitnet
    address         TEXT,                           -- NULL when the listing hides it

    -- location
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    -- point built by the database from lat/lon, so it can never disagree with them;
    -- geography = distances in metres on the real Earth
    geog            GEOGRAPHY(POINT, 4326)
                    GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED,
    h3_r8           TEXT NOT NULL,                  -- H3 cell (resolution 8, ~0.7 km²) that contains the point

    -- property
    area_m2         REAL NOT NULL CHECK (area_m2 > 0),
    bedrooms        SMALLINT,
    suites          SMALLINT,
    bathrooms       SMALLINT,
    parking         SMALLINT,
    floor_min       SMALLINT,                       -- lowest floor of the band shown (0 = 'up to 3rd'); NULL for houses
    pet_friendly    BOOLEAN,
    furnished       BOOLEAN,
    metro_near      BOOLEAN,

    -- monthly costs, R$
    rent            NUMERIC(10, 2),
    condo_fee       NUMERIC(10, 2),
    condo_included  BOOLEAN,
    iptu            NUMERIC(10, 2),
    fire_insurance  NUMERIC(10, 2),
    service_fee     NUMERIC(10, 2),
    total_cost      NUMERIC(10, 2) NOT NULL,
    cost_per_m2     NUMERIC(8, 2),

    loaded_at       TIMESTAMP NOT NULL DEFAULT now()  -- when this row was last inserted/updated
);

CREATE INDEX IF NOT EXISTS listings_geog_gix  ON listings USING GIST (geog);   -- "within X metres of ..."
CREATE INDEX IF NOT EXISTS listings_h3_idx    ON listings (h3_r8);             -- join with areas / travel_times
CREATE INDEX IF NOT EXISTS listings_bairro_idx ON listings (bairro);
CREATE INDEX IF NOT EXISTS listings_zone_idx  ON listings (zone);


-- ---------------------------------------------------------------------
-- areas: one row per H3 cell that has listings.
-- Filled in Phase 4 (features, clusters); more feature columns get added then.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS areas (
    h3_r8               TEXT PRIMARY KEY,
    geom                GEOMETRY(POLYGON, 4326) NOT NULL,   -- hexagon outline, for maps
    n_listings          INTEGER,
    median_cost_per_m2  NUMERIC(8, 2),
    median_area_m2      REAL,
    cluster_id          SMALLINT,                           -- K-Means result (Phase 4)
    cluster_label       TEXT                                -- human name, e.g. 'well-connected & expensive'
);

CREATE INDEX IF NOT EXISTS areas_geom_gix ON areas USING GIST (geom);


-- ---------------------------------------------------------------------
-- travel_times: rush-hour minutes from a work cell to a home cell (Phase 3).
-- The app's main question: "home cells within N minutes of my work cell, by <mode>".
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS travel_times (
    work_h3     TEXT NOT NULL,                  -- where the user works
    home_h3     TEXT NOT NULL,                  -- a cell with listings
    mode        TEXT NOT NULL CHECK (mode IN ('transit', 'car')),
    minutes     REAL NOT NULL CHECK (minutes >= 0),
    -- column order matters: the index behind this key serves WHERE work_h3 = ? AND mode = ?
    PRIMARY KEY (work_h3, mode, home_h3)
);


-- ---------------------------------------------------------------------
-- stations: metro / monorail / train (CPTM) stations in São Paulo city,
-- from OpenStreetMap (db/load_stations.py). One row per station *per system*:
-- an interchange like Luz has a 'metro' row and a 'train' row.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stations (
    station_id  TEXT PRIMARY KEY,               -- OSM id, e.g. 'node/123456'
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('metro', 'monorail', 'train')),
    operator    TEXT,                           -- Metrô, CPTM, ViaQuatro, ViaMobilidade, ...
    lat         DOUBLE PRECISION NOT NULL,
    lon         DOUBLE PRECISION NOT NULL,
    geog        GEOGRAPHY(POINT, 4326)
                GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED
);

CREATE INDEX IF NOT EXISTS stations_geog_gix ON stations USING GIST (geog);


-- ---------------------------------------------------------------------
-- geocode_cache: address text -> coordinates (Nominatim), so each
-- work address is looked up only once (Phase 4 recommender / chatbot).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geocode_cache (
    query           TEXT PRIMARY KEY,           -- normalised address as typed by the user
    lat             DOUBLE PRECISION,           -- NULL = Nominatim found nothing (cached too)
    lon             DOUBLE PRECISION,
    display_name    TEXT,                       -- full address Nominatim matched, to show the user
    h3_r8           TEXT,
    geocoded_at     TIMESTAMP NOT NULL DEFAULT now()
);
