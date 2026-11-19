# Telecom RF Tool v1.0.1

1. Open **Plugins → Manage and Install Plugins → Install from ZIP** in QGIS.
2. Select the ZIP and install it.
3. Add a single-point vector layer with a valid CRS.
4. Open **Plugins → Telecom RF Tool → Site to Cell Converter**.
5. Select the layer and its SiteID, azimuth, and sector fields.
6. Set the radius (default 200 m). Set a default beamwidth (65°) or select a
   field for per-sector widths. A blank width uses the default.
7. Optionally choose one fill color for all sectors and a new GeoPackage
   filename. Leave the output blank for a temporary map layer.
8. Click **Create Cells**.

Azimuth uses degrees clockwise from true north. Input can contain one row per
sector or comma-separated values within a row, in matching order.

| SiteID | Sector | Azimuth | Beamwidth |
| ------ | ------ | ------- | --------- |
| S001 | A,B,C | 0,120,240 | 65,60,75 |
| S002 | A | 180 | |

The output contains WGS84 sector polygons and the fields site_id, sector,
azimuth, beamwidth, and radius_m. The input layer is unchanged. Invalid rows
stop conversion with the source feature ID. Existing GeoPackages are preserved.
Sectors crossing the antimeridian are unsupported. 

## LTE PCI Planning

Open **Plugins → Telecom RF Tool → PCI Planning**. Select a point layer,
SiteID, and Sector. Use one row per cell, or a site row with comma-separated
sector IDs. Rows for the same site must share coordinates within 1 metre.
Start and end are inclusive LTE PCI IDs (0–503). Step 1 uses consecutive PCIs.
Steps above 1 use positive multiples within the range, so step 4 gives series
such as 4,8,12,16. Sectors are assigned in numeric or alphabetical order.

The planner treats the layer as a single LTE carrier and plans every input
cell when no existing PCI field is selected. Sites are processed in SiteID order. For each site it first
finds series that meet the requested site-to-site reuse distance, then prefers
the series with the lowest total global usage, breaking ties by the smallest
maximum usage and then lowest starting PCI. If blocked, it retries at 2 km
intervals down to 0 km. At 0 km there is no inter-site distance constraint.
Balancing is best effort and subject to available series and reuse distance.

The output preserves original attributes and adds the planned sector, PCI,
PSS ID (PCI mod 3), SSS group (PCI div 3), actual reuse distance, and reuse threshold used for the
assignment. The reuse threshold is the one applied when that site was planned,
and later sites can relax their own threshold. Save an optional GeoPackage or
leave the filename blank for a temporary point layer.

Select an existing PCI field to fill only blank or NULL values. Existing PCIs
(including 0) count toward balancing and constrain reuse even at sites processed
later. For comma-separated sectors, align PCI slots by position, e.g. 10,,12.
The planner tries to extend a compatible full-site sequence. If the existing
PCIs cannot form one, it assigns a stepped series to the blank sectors only
while avoiding existing PCIs at the same site. The output column immediately
after planned_pci contains the geodesic distance in km to the closest other
site using the same PCI in the completed plan. It is blank for a PCI with no
other site in the plan.

## LTE RSI Planning

Open **Plugins → Telecom RF Tool → RSI Planning** and map SiteID, sector
and existing RSI on a point layer. Existing values are preserved, including
zero. Align comma-separated RSI slots with sector IDs when using site rows.
Each blank cell receives its own RSI without a series constraint.

The range defaults to 0–837 for LTE and accepts a custom upper bound of 838.
The default same-site exclusion is ±9 inclusive. Existing RSI 100 blocks
91–109, and existing RSI 200 blocks 191–209. This exclusion is never relaxed
for new assignments. Existing values are preserved even if they conflict.

At the target reuse threshold (default 10 km), the lowest global usage wins,
then the farthest nearest reuse, then the lowest numeric RSI. If no candidate
qualifies, the threshold drops by 2 km at a time to a minimum of 2 km. Cells
without a candidate remain blank and are marked Unassigned. The output
includes the actual nearest same-RSI distance to another site immediately
after the planned RSI, the threshold used, and the assignment status.

## Basemaps

Choose a layer under **Plugins → Telecom RF Tool → Basemaps**. The plugin
adds it to the bottom of the project layer tree. Tile rendering requires
network access to the provider. OpenStreetMap attribution is
© OpenStreetMap contributors (https://www.openstreetmap.org/copyright).

| Map | XYZ URL |
| --- | --- |
| Google Satellite | `https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}` |
| Google Maps (Roads) | `https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}` |
| Google Hybrid | `https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}` |
| OpenStreetMap | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` |

## Cluster Creator

Use one point per site. Choose the exact cluster count or a target number
of sites per cluster. Target mode rounds the cluster count up and requires
each group to fit within the target plus or minus the selected adjustment.
Count mode starts from balanced sizes. Allowing adjustment lets spatial splits
prefer larger gaps between nearby sites within the permitted size limits.

Optionally clip to a polygon boundary. The default domain is the site extent
with a margin. Sites outside a selected boundary must be corrected before
running. Boundaries are generated by dissolving nearest-site Voronoi cells
by cluster. Shared edges lie on site bisectors in a local metric projection.
Output is a polygon layer with cluster ID and site count.

## Cluster Generator

This separate menu entry opens the bundled Cluster Generator 1.0.0 from
Senol Baskaya, using the supplied plugin's grouping-field workflow. The
source distribution's license and README are included with it.

Selecting a basemap repairs matching XYZ layers from earlier releases.

## Nearest and Far Distance Calculators

Select a site or cell point layer, SiteID, and a match field such as PCI.
Each input row gets the nearest or farthest other SiteID sharing its match
value. Values are compared as trimmed text, preserving zero. Other rows
from the same SiteID are excluded. The output retains the input fields and
geometry and adds matched_site, distance_km (WGS84 ellipsoidal), and
match_status. Blank values and unmatched rows have empty distance fields.
For equal distances the alphabetically first SiteID wins. Leave the output
path blank for a temporary layer or enter a new GeoPackage filename.

## License

Telecom RF Tool is distributed under GNU GPL version 2. The bundled
Cluster Generator retains its upstream notice in vendor_generator/LICENSE.

## Release 1.0.1

Experimental metadata is disabled. Every tool and XYZ basemap has its own original SVG icon, based on the supplied visual themes. Best NBR Creator and NBR Selector are visibly disabled until implemented.
