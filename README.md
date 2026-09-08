# slowframes → Threads

Очередь постов для личного Threads Артема (@shoot.kin): крючок + карусель из
4–5 кадров slowframes + первый ответ в ветке «где снято и на что».

- `queue/<дата>-<id>/` — `post.json` и кадры `1.jpg…5.jpg`. Порядок публикации — по имени папки.
- `state/published.json` — что уже вышло (пишется ботом, руками не править).
- `publish.py` — публикатор без зависимостей. `python3 publish.py --dry` показывает следующий пост.
- `.github/workflows/daily.yml` — один пост в день в 11:00 UTC.

Черновики собираются на локальном столе `_oneoffs/2026-09-08-threads-stol`
(кнопка «Беру»), `export.py` оттуда кладёт одобренные в `queue/` и пушит.
Токен `THREADS_RU_ACCESS_TOKEN` — тот же, что у EastRide, живёт 60 дней.
