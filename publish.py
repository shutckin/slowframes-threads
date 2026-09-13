#!/usr/bin/env python3
"""Публикует очередной пост из очереди в личный Threads (@shoot.kin).

Схема как у EastRide (tripride/scripts/social-daily.ts), только на Python
без зависимостей: очередь и кадры лежат в этом репозитории, GitHub Actions
запускает скрипт по расписанию, картинки Threads забирает по raw-ссылкам
GitHub. Локальная машина для публикации не нужна.

Очередей две, аккаунт один:
  slowframes — queue/<id>/post.json + 1.jpg … 5.jpg, карусель кадров;
               {"hook": "...", "reply": "...", "images": ["1.jpg", ...]}
  atlas      — queue-atlas/<id>/post.json, текстовые посты LATAM Atlas;
               {"text": "...", "reply": "..."}; ссылка на сайт — в ответе.
Порядок — по имени папки (числовой префикс). У каждой очереди свой файл
в state/, он помнит, что уже вышло; повтор невозможен: отметка пишется
сразу после публикации.

Ритм — раз в MIN_INTERVAL_DAYS дней (по умолчанию три). Расписание в
GitHub Actions ежедневное, а интервал держит сам скрипт: cron вида
«*/3» на стыке месяцев даёт то три дня, то один, и очередь съезжает.
Здесь же считается разница с последней публикацией, так что пропущенный
запуск ничего не ломает. Допуск SLACK_DAYS: GitHub запускает cron с
плавающей задержкой до часа, и без допуска пост, вышедший в 14:47,
через три дня в 14:40 считался бы «рано» и съезжал на сутки.

Атлас не выходит в один день со slowframes: в прогоне slowframes идёт
первым, и если он только что опубликовал, атлас ждёт завтрашнего запуска.
Так посты чередуются, лента не забивается двумя за день.

Пауза: файл PAUSED в папке очереди — очередь молчит. Атлас стоит на
паузе до выкатки сайта (иначе переходы не посчитаются), снять — удалить
queue-atlas/PAUSED.

Лимит Threads — 500 знаков на пост. Длиннее — скрипт падает до отправки,
в том числе при холостом прогоне.

  python3 publish.py                  # slowframes: следующий пост, если срок подошёл
  python3 publish.py --queue atlas    # то же для атласа
  python3 publish.py --dry            # показать, что ушло бы, ничего не отправлять
  python3 publish.py --force          # опубликовать сейчас, не дожидаясь срока
  python3 publish.py --id <id>        # конкретный пост вне очереди (и вне паузы)
"""
import calendar, json, os, sys, time, pathlib, urllib.request, urllib.parse, urllib.error

BASE = 'https://graph.threads.net/v1.0'
HERE = pathlib.Path(__file__).parent
RAW = os.environ.get('RAW_BASE', 'https://raw.githubusercontent.com/shutckin/slowframes-threads/main')
TOKEN = os.environ.get('THREADS_RU_ACCESS_TOKEN', '')
DRY = '--dry' in sys.argv
FORCE = '--force' in sys.argv or '--id' in sys.argv
MIN_DAYS = float(os.environ.get('MIN_INTERVAL_DAYS', '3'))
SLACK_DAYS = 0.25
MAX_CHARS = 500

QUEUES = {
    'slowframes': {'dir': 'queue', 'state': 'state/published.json'},
    # avoid — не публиковать, если другая очередь выходила меньше AVOID_DAYS назад
    'atlas': {'dir': 'queue-atlas', 'state': 'state/published-atlas.json', 'avoid': 'slowframes'},
}
AVOID_DAYS = 0.5

def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

QUEUE = arg('--queue', 'slowframes')
if QUEUE not in QUEUES: raise SystemExit(f'неизвестная очередь {QUEUE}: {", ".join(QUEUES)}')
CFG = QUEUES[QUEUE]
QDIR = HERE/CFG['dir']
STATE = HERE/CFG['state']

def call(path, params, method='GET'):
    params = {**params, 'access_token': TOKEN}
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f'{BASE}{path}?{data.decode()}' if method == 'GET' else f'{BASE}{path}',
                                 data=None if method == 'GET' else data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: j = json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f'threads {path}: {e.code} {e.read().decode()[:300]}')
    if 'error' in j: raise SystemExit(f"threads {path}: {j['error'].get('message')}")
    return j

def wait_ready(cid):
    # Threads готовит контейнер асинхронно; на неготовый отвечает так, будто
    # его нет. Карусель из пяти кадров может собираться около минуты.
    for delay in (3, 5, 10, 20, 30, 30):
        time.sleep(delay)
        j = call(f'/{cid}', {'fields': 'status,error_message'})
        if j.get('status') == 'FINISHED': return
        if j.get('status') == 'ERROR': raise SystemExit(f"контейнер {cid} отклонён: {j.get('error_message')}")
    raise SystemExit(f'контейнер {cid} не готов после 98 секунд')

def load(path):
    return json.loads(path.read_text()) if path.exists() else {}

def pick():
    done = load(STATE)
    if '--id' in sys.argv:
        return arg('--id'), done
    for d in sorted(p.name for p in (QDIR.iterdir() if QDIR.exists() else []) if (p/'post.json').exists()):
        if d not in done: return d, done
    return None, done

def days_since_last(done):
    times = [v.get('at') for v in done.values() if v.get('at')]
    if not times: return None
    last = max(times)
    # Метки в UTC, поэтому timegm, а не mktime (тот считает по местному поясу)
    return (time.time() - calendar.timegm(time.strptime(last, '%Y-%m-%dT%H:%M:%SZ'))) / 86400

def due(done):
    """Пора ли публиковать: срок подошёл и соседняя очередь не выходила сегодня."""
    gap = days_since_last(done)
    if gap is not None and gap < MIN_DAYS - SLACK_DAYS:
        return False, f'рано: с прошлой публикации {gap:.1f} дн., интервал {MIN_DAYS:g} дн.'
    if CFG.get('avoid'):
        other = days_since_last(load(HERE/QUEUES[CFG['avoid']]['state']))
        if other is not None and other < AVOID_DAYS:
            return False, f'сегодня уже вышел {CFG["avoid"]} ({other * 24:.0f} ч назад), ждём завтра'
    return True, ''

def check_length(post):
    for field in ('text', 'hook', 'reply'):
        n = len(post.get(field) or '')
        if n > MAX_CHARS: raise SystemExit(f'поле {field}: {n} знаков, лимит Threads {MAX_CHARS}')

def publish_container(me, params):
    cid = call(f'/{me}/threads', params, 'POST')['id']
    wait_ready(cid)
    return call(f'/{me}/threads_publish', {'creation_id': cid}, 'POST')['id']

def main():
    pid, done = pick()
    if (QDIR/'PAUSED').exists() and '--id' not in sys.argv:
        print(f'очередь {QUEUE} на паузе (файл {CFG["dir"]}/PAUSED)'); return
    if not pid: print(f'очередь {QUEUE} пуста'); return
    ready, why = due(done)
    if not ready and not FORCE:
        print(f'{QUEUE}: {why}; следующий пост — {pid}')
        return
    post = json.loads((QDIR/pid/'post.json').read_text())
    check_length(post)
    images = post.get('images') or []
    urls = [f'{RAW}/{CFG["dir"]}/{pid}/{name}' for name in images]
    body = post.get('hook') if images else post.get('text')
    print(f'{QUEUE} / пост {pid}: {len(urls)} кадров\n{body}\n---\n{post.get("reply", "")}')
    if DRY:
        for u in urls: print(' ', u)
        return
    if not TOKEN: raise SystemExit('THREADS_RU_ACCESS_TOKEN не задан')
    me = call('/me', {'fields': 'id'})['id']
    if images:
        children = []
        for u in urls:
            c = call(f'/{me}/threads', {'media_type': 'IMAGE', 'image_url': u, 'is_carousel_item': 'true'}, 'POST')['id']
            wait_ready(c); children.append(c)
        pub = publish_container(me, {'media_type': 'CAROUSEL', 'children': ','.join(children), 'text': body})
    else:
        pub = publish_container(me, {'media_type': 'TEXT', 'text': body})
    link = call(f'/{pub}', {'fields': 'permalink'}).get('permalink', '')
    # Отметку пишем до ответа: если ответ упадёт, пост не выйдет второй раз
    done[pid] = {'post': pub, 'permalink': link, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    print('опубликовано:', link)
    if post.get('reply'):
        rid = publish_container(me, {'media_type': 'TEXT', 'text': post['reply'], 'reply_to_id': pub})
        done[pid]['reply'] = rid; STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
        print('ответ в ветке:', rid)

if __name__ == '__main__':
    main()
