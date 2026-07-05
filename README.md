# Movie Tracker

A personal TV/movie tracker for two people (a lightweight TV Time replacement).
Search TMDB, track shows, check off episodes as you watch, rate 1-5 stars, and
see a shared activity feed of what the other person's been watching —
gated by a public/private toggle per show.

## User flow
1. Sign up / log in
2. Search a show → click a result
3. Show detail page shows the TMDB description, season/episode list, and any
   **public** comments/ratings from other users
4. For TV shows: check off episodes as you watch them. The "Next up" widget
   tracks where you left off automatically.
5. Rate it (1-5 stars), optionally leave a comment, mark it public or private
6. It appears in **My Library**. Public ratings and episode check-ins show up
   in **Activity** for the other person to see.

## Setup

```bash
cd movie_tracker
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and paste in your TMDB_ACCESS_TOKEN
# (Settings -> API -> Request an API Key on themoviedb.org, use the "API Read Access Token")

python run.py
```

Visit http://localhost:5000. Have your second person sign up their own account
on the same running instance.

## Project structure

```
app/
  __init__.py         # app factory, blueprint registration, db.create_all()
  config.py            # env-driven config
  extensions.py        # db, login_manager singletons
  models.py            # User, LibraryItem, EpisodeCheckIn
  services/tmdb.py     # search_shows(), get_show_details(), get_season_episodes()
  auth/                # signup, login, logout
  library/             # personal library view + delete
  shows/               # search, show detail, rate, episode check-in, status
  activity/            # shared public activity feed
  templates/
  static/style.css
```

## Data model notes

- **`LibraryItem`** — one row per (user, show). Holds `status`
  (watching/completed/plan_to_watch/dropped), optional `rating` (1-5,
  nullable — you can track a show for months before rating it), optional
  `comment`, and `is_public`.
- **`EpisodeCheckIn`** — one row per watched episode, per user. Deleting the
  row = marking it unwatched (that's what the checkbox toggle does under the
  hood via `POST /show/tv/<id>/episode/checkin`). Has its own `is_public`
  flag that's kept in sync with the show's `LibraryItem.is_public` whenever
  you save a rating — so you only manage one visibility toggle per show, not
  per episode.
- **"Next up"** is computed on the fly in `shows/routes.py`
  (`_next_episode_for`): walks the season list from TMDB in order and
  returns the first episode number without a matching `EpisodeCheckIn` row.
- **Activity feed** (`/activity`) unions two queries — public `LibraryItem`
  rows (rating events) and public `EpisodeCheckIn` rows (watch events) —
  sorted by timestamp. Private items never appear here regardless of who's
  viewing.
- Public comments on a show's detail page are read the same way as before:
  every user's `LibraryItem` for that `tmdb_id` where `is_public = True`.
- Currently uses SQLite via SQLAlchemy for simplicity. Swapping to Postgres
  (e.g. Supabase) is a one-line `DATABASE_URL` change — nothing else in the
  code changes. Swapping to DynamoDB later: `LibraryItem` maps to a table
  with `user_id` as partition key and `tmdb_id#media_type` as sort key,
  `EpisodeCheckIn` to a table with `user_id` as partition key and
  `tmdb_show_id#season#episode` as sort key — same pattern as your nanobot
  pipeline's `instance_id`-based lookups.

## Heads up: fresh database needed

This update added new columns (`status`) and a new table (`EpisodeCheckIn`),
and made `LibraryItem.rating` nullable. Delete any existing
`movie_tracker.db` / `instance/movie_tracker.db` before running — `db.create_all()`
will rebuild it from the new models on first launch. There's no migration
path from the old schema; for a personal 2-user app this is the simplest option.

## Next steps to build out
- Pagination on search results
- "Already in library" indicator on search results
- Password reset flow
- Mark a whole season watched in one click
- Swap SQLite → Postgres/Supabase for anywhere-access between the two of you
