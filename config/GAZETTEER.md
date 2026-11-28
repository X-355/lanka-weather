# config/gazetteer.csv — place finder for the Weather tab

1,765 Sri Lankan towns and villages with Sinhala and Tamil names, postcodes and coordinates.

Source: [madurapa/sri-lanka-provinces-districts-cities](https://github.com/madurapa/sri-lanka-provinces-districts-cities)
(MIT licence, © 2016 Maduka Jayalath), 2,155 entries. Cleaned as follows:

1. **Fallback coordinates removed.** 287 entries shared their exact coordinates with other places (39 sat on
   Kurunegala town), i.e. they had been geocoded to a town centre. Only the place that town centre belongs to was kept.
2. **District from location.** Each point was tested against the district boundaries (the same map the site uses,
   geo-referenced so that all 25 district capitals fall inside their own district).
   - Inside, or just off the coast of, the listed district: kept (1,678).
   - In a neighbouring district within 12 km of the listed one: kept, with the district it lies in
     (`district`) and the listed one kept as `postal_district` (87). These are mostly postal-area labels.
   - More than 12 km from the listed district: dropped (103), because the coordinates most likely belong to a
     different place with the same name.

The 166 towns in `places.csv` (which get the nightly model forecast and the backtest) are listed in the place finder too.

## Grama Niladhari divisions — `config/gn_divisions.csv` (monthly)

Sri Lanka's ~14,000 GN divisions from the official administrative boundaries (COD-AB, Survey Department of
Sri Lanka and OCHA, [HDX](https://data.humdata.org/dataset/cod-ab-lka), CC BY-IGO). Each division is placed at
a point guaranteed to lie inside it, filed under its official district, with Sinhala and Tamil names where the
dataset has them. A division is left out of the finder when a postal town or OpenStreetMap place with the same
name lies within 3 km.

## OpenStreetMap places — `config/osm_places.csv` (monthly)

Named city/town/suburb/village/hamlet/neighbourhood points from OpenStreetMap (© OpenStreetMap contributors,
ODbL) via the Overpass API, district by point-in-polygon against `config/districts.geojson`; places outside
Sri Lanka and duplicates of postal towns within 3 km are dropped.
