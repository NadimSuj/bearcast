<p align="center">
  <img src="web/public/bearcast-logo.png" alt="BearCast logo" width="140">
</p>

<h1 align="center">BearCast</h1>
<p align="center"><b>A monthly outlook for Florida black bears: where conflicts with people are likely, where bears have been, and where they could go.</b></p>

---

## What is BearCast?

Florida's black bears share more and more of the state with people. When a bear gets into garbage, raids a
bird feeder, damages property, or threatens pets or people, someone calls the Florida Fish and Wildlife
Conservation Commission (FWC). FWC has logged more than 100,000 bear-related calls over the years.

BearCast turns those records, together with maps of habitat, housing, roads and land cover, into an
interactive map of Florida that answers three questions:

1. **Where are bear conflicts most likely next month?** A forecast, updated every month.
2. **Where have bears been, and where do they like to live?** Recent reports, likely habitat, and the
   corridors bears use to travel between populations.
3. **Where could a bear go from here?** An animation of simulated bears wandering for two weeks, using
   movement patterns learned from GPS-collared bears.

It's meant for curious residents, students, planners, and anyone who wants to understand bear activity in
Florida at a glance.

> **BearCast is an independent project, not an official FWC product.** Its forecasts are statistical
> estimates. They can't tell you whether a bear is in your yard right now.
> If a bear is threatening people, pets or property, see [If you meet a bear](#if-you-meet-a-bear).

---

## The basics: hexagons and "conflicts"

**The map is made of hexagons.** Florida is divided into about 31,000 hexagons, each about
**2 square miles** (5 km²), roughly the size of a neighborhood or a small town. Every number on the map is
for one hexagon. Hexagons are used because they tile neatly and every neighbor is the same distance away.

**A "conflict" is a call to FWC about a bear causing a problem**: getting into garbage or other food around
homes, damaging property, going after pets or livestock, or acting in a way that worries people for their
safety. Simple sightings ("I saw a bear cross the road") are counted separately.

---

## Reading the map

### The screen at a glance

| Part | What it does |
|---|---|
| **Forecast period** card (top of the sidebar) | The month the forecast is for, and the date of the latest reports it uses. |
| **Map layer** | Pick what the hexagons show (explained below). Only one at a time. |
| **Corridor routes between populations** | A checkbox that draws the most likely travel routes between bear populations as lines. |
| **Base map** | Change the background map (Color, Artistic, Light). |
| **Reading the …** | A plain explanation and color key for the layer you picked. |
| **Simulated movement** | Pick a starting point and watch simulated bears wander. |
| **The map** | Drag to move, scroll or pinch to zoom. Hover a hexagon for a quick summary; **click** it for full details. |

The map stays on Florida: you can't scroll away or zoom out past the state.

### The map layers

**Conflict risk next month**
The forecast. Each colored hexagon is ranked against every other hexagon in Florida by how many conflict
calls are expected there next month.

| Color | Meaning |
|---|---|
| Dark rust | **Very high**: the top 1% of hexagons statewide |
| Amber | **High**: the top 5% |
| Sand | **Elevated**: the top 20% |
| (no color) | Lower risk |

*How good is it?* In a test on 22 months the model had never seen (January 2025 to October 2026), the 1% of
hexagons it flagged as very high held about **half** of all conflict calls, and the top 5% held about
**84%**.

**Emerging risk**
Places with **no conflict calls in the past year** that look most likely to start having them. These are
often spots where bears are moving into new neighborhoods. In testing, this layer caught about two-thirds
of first-time conflicts. Dark blue hexagons have never had a conflict on record; light blue ones had one
more than a year ago.

**Recent bear activity**
Where bears have actually been reported lately: sightings, conflict calls and roadkill from the **last 90
days**. Activity spreads a little to neighboring hexagons, because a bear reported on one street was probably
nearby too. Darker = more reports.

**Likely bear habitat**
Where bears would *prefer* to live, based on hundreds of radio- and GPS-collared bears tracked by
researchers (1983–2014) and today's landscape: forests, wetlands, conservation land, few houses. Shown as a
percentile, so dark green = among the best bear habitat in the state.

**Movement corridors**
The most likely routes for bears travelling **between** Florida's main bear populations. They follow good
habitat and avoid towns. Darkest brown is the single best route; lighter shades are slightly longer
alternatives (up to 15% "costlier" for a bear). Where a corridor crosses a busy road is a natural candidate
for a wildlife crossing. Population cores themselves are left blank on this layer.

**Road risk**
Where good bear habitat meets heavy traffic: the places a bear is most likely to be hit by a car. In
testing, the top 10% of road hexagons held 38% of recorded bear roadkill (2010–2025). Darker red = higher
risk.

### Clicking a hexagon

Click any hexagon to open its details panel:

| Section | What it tells you |
|---|---|
| **Conflict forecast** | The risk level (very high / high / elevated / low), the number of **expected calls** next month, and the hexagon's **statewide rank** (#1 = highest risk in Florida). Expected calls are an average, so 0.25 means "about one call every four months like this one". |
| **Emerging-risk rank** | Shown only for hexagons with no conflict in the last year: how it ranks among those quiet places. |
| **Recent activity** | How many bear reports came from this hexagon in the last 90 days, when the last one was, and how busy the surrounding area has been. |
| **FWC range** | How often FWC finds bears here: *frequent*, *common*, *occasional* or *rare* (or outside the mapped bear range). |
| **Habitat percentile** | How good the habitat is compared with the rest of Florida (90% = better than 90% of hexagons). |
| **Population core** | Whether the hexagon is inside one of the main bear populations. |
| **Corridor route** | If it lies on a travel corridor: which populations it connects and how close to the best route it is. |
| **Road risk** | The chance of bear–vehicle collisions here, and the traffic on the busiest road (vehicles per day). |
| **Homes, conservation land, natural cover** | Housing density, how much land is protected, and how much is still forest, wetland or other natural cover. |

### Simulated movement

Choose a starting point, or click one of the dark dots on the map. The map flies there and **25 simulated
bears** wander for **14 days**, each drawn as a colored track. Use the play button and the slider to replay
or scrub through the days. The orange hexagons show how many of the simulated bears reached each spot: the
darker, the more of them got there.

There are two kinds of starting points:

- **Population Cores**: the heart of each of Florida's main bear populations (Big Cypress,
  Eglin–Apalachicola, Ocala/St. Johns and others). Shows how bears move around their home ground.
- **Recent Activity Hotspots**: the 10 hexagons with the most bear reports in the 90 days before the
  simulations were made (#1 had the most). Shows where a bear seen there might go next.

The movement rules come from GPS-collared bears at Camp Blanding in northeast Florida: how far bears
typically walk each hour, how they turn, and how they react to forest, wetlands, open land, towns and busy
roads. **These are simulated bears, not real animals.** They show typical day-to-day wandering, not the
long journeys young bears sometimes make when they leave home.

### Base maps

- **Color**: a detailed, colorful street map (the default).
- **Artistic**: a hand-painted watercolor style. It looks lovely, but roads and places are simplified, so
  switch back to Color or Light for detail.
- **Light**: a pale, minimal map that lets the hexagon colors stand out most.

---

## What BearCast can and can't tell you

- **It shows chances, not certainties.** A "very high" hexagon is one of the likeliest places in Florida for a
  conflict next month, not a guarantee. A low-risk hexagon can still have a bear visit.
- **Reports depend on people.** A bear in an empty forest goes unreported; a bear in a busy suburb gets
  called in. So the forecast is about conflicts *with people*, and "no reports" doesn't mean "no bears".
- **Busy years run high.** In record-breaking years the model tends to expect fewer calls than actually
  happen. The *ranking* of places stays reliable, and the forecast is recalibrated every month against recent
  results.
- **The habitat model learned from older tracking data** (1983–2014). Landscapes change; recent development
  may not be fully reflected.
- **Movement rules come from one study area** and don't include long-distance moves.
- **It updates monthly**, after FWC publishes the previous month's calls. It isn't a live tracker.
- **No iNaturalist data** is used anywhere in this project.

---

## If you meet a bear

BearCast is for understanding patterns. For a real bear situation:

- **Emergency** (a bear is attacking or someone is hurt): call **911**.
- **A bear is threatening people, pets or property**, or you see a sick, injured, orphaned or dead bear, or
  someone feeding or harming bears: call FWC's **Wildlife Alert Hotline, 888-404-FWCC (3922)**.
- **Ongoing conflicts** (a bear keeps coming back): contact your FWC regional office at
  [myfwc.com/contact](https://myfwc.com/contact).
- **Prevent problems**: secure garbage, remove bird feeders while bears are active, feed pets indoors, and
  clean grills. See FWC's [Living in Bear Country](https://myfwc.com/wildlifehabitats/wildlife/bear/living/)
  and [BearWise](https://bearwise.org).

---

## How it works (in brief)

1. **Collect**: FWC bear calls, roadkill, capture and GPS-tracking records, plus housing (SILVIS Lab),
   conservation land (FNAI), roads and traffic (FDOT) and land cover (FNAI/FWC Cooperative Land Cover).
2. **Clean**: remove duplicates, sort each call into a type (conflict, sighting, and so on), and place it in
   its hexagon and month.
3. **Learn**: a machine-learning model (LightGBM) studies 2016–2024 to learn what each hexagon's recent
   history and landscape say about next month's conflicts. Separate models map habitat, corridors, road risk
   and bear movement.
4. **Store and serve**: results go into a PostgreSQL/PostGIS database, served to the map by a small Python
   API (FastAPI).
5. **Show**: the web map (React, MapLibre and deck.gl) draws the hexagons, routes and animated tracks.
6. **Repeat monthly**: a monthly job downloads the newest FWC calls, checks how last month's forecast did,
   and makes the next one.

The reasoning behind each choice is written up in [`docs/`](docs/), one file per phase.

---

## Running it yourself

You'll need Python 3.11+, Node.js 20.19+, and PostgreSQL with PostGIS. On a Mac:
`brew install postgresql@18 postgis node`.

```bash
# 1. Python environment
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Database: create it, then copy .env.example to .env and set DATABASE_URL
createdb bearcast
cp .env.example .env
python -m bearcast.db.migrate        # create the tables
python -m bearcast.db.load           # load the processed data and model outputs

# 3. API (leave running)
uvicorn bearcast.api.app:app --reload          # docs at http://127.0.0.1:8000/docs

# 4. Web map, in a second terminal
cd web
npm install
echo "VITE_MAPTILER_KEY=your-key" > .env.local   # optional: Color/Artistic base maps (free key at maptiler.com)
npm run dev                                       # open http://localhost:5173
```

Building the data and models from scratch (downloads, cleaning, training) is described step by step in
[`docs/setup_and_data.md`](docs/setup_and_data.md) and the phase write-ups in [`docs/`](docs/). After that,
one command keeps everything current each month:

```bash
python -m bearcast.jobs.monthly
```

Run the tests with `python -m pytest -q`. The database tests need `TEST_DATABASE_URL` in `.env`, pointing
at a separate test database.

### Project layout

```
bearcast/
├── src/bearcast/
│   ├── ingest/     download FWC and other source data
│   ├── clean/      clean and classify bear calls
│   ├── features/   hexagon grid, history and landscape features, recent activity
│   ├── models/     train, test and run the conflict forecast
│   ├── bears/      habitat, corridors, road risk, movement simulation
│   ├── db/         database migrations, loader and queries
│   ├── jobs/       the monthly update job
│   └── api/        the FastAPI app the map talks to
├── db/migrations/  database schema
├── web/            the map (React + TypeScript + Vite)
├── notebooks/      exploration and result maps
├── docs/           decisions and setup notes, one file per phase
└── tests/
```

---

## Data sources and credits

- **Florida Fish and Wildlife Conservation Commission (FWC)**: bear-related calls, roadkill, captures,
  telemetry and bear range. FWC asks that products acknowledge it as the data source.
- **SILVIS Lab, University of Wisconsin**: housing density (1990–2020).
- **Florida Natural Areas Inventory (FNAI)**: conservation lands.
- **Florida Department of Transportation (FDOT)**: roads and traffic counts.
- **FNAI/FWC Cooperative Land Cover**: land cover.
- **Base maps**: MapTiler, CARTO and © OpenStreetMap contributors.
