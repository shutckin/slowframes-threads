# slowframes + LATAM Atlas → Threads

Очередь постов для личного Threads Артема (@shoot.kin): крючок + карусель из
4–5 кадров slowframes + первый ответ в ветке «где снято и на что».

- `queue/<номер>-<id>/` — `post.json` и кадры `1.jpg…5.jpg`. Публикуются по возрастанию номера.
- `state/published.json` — что уже вышло (пишется ботом, руками не править).
- `publish.py` — публикатор без зависимостей. `python3 publish.py --dry` показывает следующий пост.
- `.github/workflows/daily.yml` — запуск ежедневно в 11:00 UTC, публикация раз в три дня
  (`MIN_INTERVAL_DAYS`). Вручную: Actions → threads-daily → Run workflow, `dry=false`,
  `force=true` чтобы выпустить пост раньше срока.

Черновики собираются на локальном столе `_oneoffs/2026-09-08-threads-stol`
(кнопка «Беру»), `export.py` оттуда кладёт одобренные в `queue/` и пушит.
Токен `THREADS_RU_ACCESS_TOKEN` — тот же, что у EastRide, живёт 60 дней.

## Очередь LATAM Atlas (с 13.09.2026)

- `queue-atlas/<номер>-<id>/post.json` — `{"text": "...", "reply": "..."}`: текстовый пост-вопрос,
  ссылка на latamatlas.com первым ответом в ветке. Все ссылки с `latamatlas.com/th/…` (короткие ссылки сайта сами дописывают метку `?from=threads`) — по ним
  сайт считает переходы (цель проверки гипотезы — 100 за 30 дней, см. latam-atlas/docs/FOUNDATION.md).
- `state/published-atlas.json` — что вышло из атласа.
- Ритм с 01.10.2026 — каждый день, кроме дней slowframes: в ленте что-то выходит ежедневно, но не парой.
- Пауза снята 29.09.2026: latamatlas.com выкачен, короткие ссылки и статистика работают. Поставить на
  паузу снова — создать пустой файл `queue-atlas/PAUSED`.
- Лимит Threads 500 знаков; длиннее — `publish.py` падает ещё на холостом прогоне.
