# Deploying BearCast

BearCast runs online as three free pieces:

| Piece | Host | Address (if the names are free) |
|---|---|---|
| Database (Postgres + PostGIS) | [Neon](https://neon.com), free plan | private connection string |
| API (FastAPI) | [Render](https://render.com) web service, free plan | `https://bearcast-api.onrender.com` |
| Web map (static files) | Render static site, free | `https://bearcast.onrender.com` |

`render.yaml` in the repo root describes both Render services, so Render sets them up from the repo.

**Free-plan trade-offs**
- The API sleeps after 15 minutes without visitors; the first visit after that waits about a minute while it
  wakes. The map shows a "waking up" note meanwhile.
- Neon's database pauses after 5 minutes idle and resumes in under a second.
- Neon's free plan allows 1 GB of storage per project; BearCast needs much less.

## 1. Check the database size

```bash
psql -d bearcast -c "SELECT pg_size_pretty(pg_database_size('bearcast'));"
```

Anything well under 1 GB fits Neon's free plan.

## 2. Create the Neon database

1. Sign up at [neon.com](https://neon.com) (you can log in with GitHub).
2. Create a project named **bearcast**: pick the newest Postgres version offered, and the region
   **AWS US West 2 (Oregon)**, which is next to Render's default region.
3. On the project dashboard, click **Connect**, turn **Connection pooling off**, and copy the connection
   string. It looks like `postgresql://neondb_owner:...@ep-....us-west-2.aws.neon.tech/neondb?sslmode=require...`
4. Add it to your `.env` (in quotes, because it contains `&`):

   ```
   NEON_DATABASE_URL='postgresql://neondb_owner:...@ep-....aws.neon.tech/neondb?sslmode=require&channel_binding=require'
   ```

## 3. Fill the Neon database

These are the same commands as for the local database, pointed at Neon. `migrate` creates PostGIS and the
tables; `load` copies in the processed data and model outputs from `data/`.

```bash
python -m bearcast.db.migrate --url-var NEON_DATABASE_URL
python -m bearcast.db.load --url-var NEON_DATABASE_URL
```

The table sizes printed at the end should match your local database.

## 4. Create the Render services

1. Sign up at [render.com](https://render.com) with GitHub, and allow it to see the `bearcast` repo.
2. **New → Blueprint**, choose `NadimSuj/bearcast`, branch `master`. Render reads `render.yaml` and lists
   two services, **bearcast-api** and **bearcast**.
3. Fill in the four values it asks for:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` (bearcast-api) | the Neon connection string (no quotes) |
   | `CORS_ORIGINS` (bearcast-api) | `https://bearcast.onrender.com` |
   | `VITE_API_BASE` (bearcast) | `https://bearcast-api.onrender.com` |
   | `VITE_MAPTILER_KEY` (bearcast) | your MapTiler key |

4. Click **Apply**. The first builds take a few minutes.

**If Render gave a service a different address** (for example `bearcast-xyz1.onrender.com`, when the plain
name is taken), open each service's **Environment** page, correct `CORS_ORIGINS` and `VITE_API_BASE` to the
real addresses, save, and redeploy the web map (**Manual Deploy → Deploy latest commit**). The web map reads
`VITE_API_BASE` when it's built, so it needs that redeploy.

## 5. Allow the new address in MapTiler

In MapTiler's dashboard, open your API key and add the web map's address (e.g. `https://bearcast.onrender.com`)
to its **allowed HTTP origins**, next to `localhost`. Otherwise the Color and Artistic base maps won't load there.

## 6. Check it

- `https://bearcast-api.onrender.com/api/health` shows `{"status":"ok", ...}` (the first visit may take a minute).
- `https://bearcast.onrender.com` shows the map; click a hexagon to check the details panel.

## Monthly updates

Render redeploys by itself whenever you push to `master`. For new data, run the monthly job against Neon,
then refresh your local copy too:

```bash
python -m bearcast.jobs.monthly --url-var NEON_DATABASE_URL   # the online database is the main one now
python -m bearcast.db.load                                    # copy the same outputs into the local database
```

Run the job against Neon only, not against both databases. The job saves each month's score to a local file
that the next forecast uses, so running it twice would count that month twice. The plain `load` above just
copies the finished outputs and skips anything it has already loaded.
