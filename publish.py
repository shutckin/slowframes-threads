#!/usr/bin/env python3
"""Публикует очередной пост из очереди в личный Threads (@shoot.kin).

Схема как у EastRide (tripride/scripts/social-daily.ts), только на Python
без зависимостей: очередь и кадры лежат в этом репозитории, GitHub Actions
запускает скрипт по расписанию, картинки Threads забирает по raw-ссылкам
GitHub. Локальная машина для публикации не нужна.

Очередь: queue/<id>/post.json + 1.jpg … 5.jpg. post.json:
  {"hook": "...", "reply": "...", "images": ["1.jpg", ...]}
Порядок — по имени папки (числовой префикс). state/published.json помнит,
что уже вышло; повтор невозможен: отметка пишется сразу после публикации.

Ритм — раз в MIN_INTERVAL_DAYS дней (по умолчанию три). Расписание в
GitHub Actions ежедневное, а интервал держит сам скрипт: cron вида
«*/3» на стыке месяцев даёт то три дня, то один, и очередь съезжает.
Здесь же считается разница с последней публикацией, так что пропущенный
запуск ничего не ломает.

Пост = карусель (4–5 кадров) + первый ответ в ветке с подписью, где снято
и на что. Ответ — отдельный пост с reply_to_id.

  python3 publish.py            # опубликовать следующий пост, если срок подошёл
  python3 publish.py --dry      # показать, что ушло бы, ничего не отправлять
  python3 publish.py --force    # опубликовать сейчас, не дожидаясь срока
  python3 publish.py --id <id>  # конкретный пост вне очереди
"""
import json, os, sys, time, pathlib, urllib.request, urllib.parse, urllib.error

BASE = 'https://graph.threads.net/v1.0'
HERE = pathlib.Path(__file__).parent
RAW = os.environ.get('RAW_BASE', 'https://raw.githubusercontent.com/shutckin/slowframes-threads/main')
TOKEN = os.environ.get('THREADS_RU_ACCESS_TOKEN', '')
STATE = HERE/'state/published.json'
DRY = '--dry' in sys.argv
FORCE = '--force' in sys.argv or '--id' in sys.argv
MIN_DAYS = float(os.environ.get('MIN_INTERVAL_DAYS', '3'))

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

def pick():
    done = json.loads(STATE.read_text()) if STATE.exists() else {}
    if '--id' in sys.argv:
        return sys.argv[sys.argv.index('--id') + 1], done
    q = HERE/'queue'
    for d in sorted(p.name for p in (q.iterdir() if q.exists() else []) if (p/'post.json').exists()):
        if d not in done: return d, done
    return None, done

def due(done):
    """Пора ли публиковать: с последней публикации прошло MIN_DAYS дней."""
    times = [v.get('at') for v in done.values() if v.get('at')]
    if not times: return True, 0.0
    last = max(times)
    gap = (time.time() - time.mktime(time.strptime(last, '%Y-%m-%dT%H:%M:%SZ'))) / 86400
    return gap >= MIN_DAYS, gap

def main():
    pid, done = pick()
    if not pid: print('очередь пуста'); return
    ready, gap = due(done)
    if not ready and not FORCE:
        print(f'рано: с прошлой публикации {gap:.1f} дн., интервал {MIN_DAYS:g} дн. '
              f'Следующий пост {pid} выйдет через {MIN_DAYS - gap:.1f} дн.')
        return
    post = json.loads((HERE/'queue'/pid/'post.json').read_text())
    urls = [f'{RAW}/queue/{pid}/{name}' for name in post['images']]
    print(f'пост {pid}: {len(urls)} кадров\n{post["hook"]}\n---\n{post["reply"]}')
    if DRY:
        for u in urls: print(' ', u)
        return
    if not TOKEN: raise SystemExit('THREADS_RU_ACCESS_TOKEN не задан')
    me = call('/me', {'fields': 'id'})['id']
    children = []
    for u in urls:
        c = call(f'/{me}/threads', {'media_type': 'IMAGE', 'image_url': u, 'is_carousel_item': 'true'}, 'POST')['id']
        wait_ready(c); children.append(c)
    car = call(f'/{me}/threads', {'media_type': 'CAROUSEL', 'children': ','.join(children), 'text': post['hook']}, 'POST')['id']
    wait_ready(car)
    pub = call(f'/{me}/threads_publish', {'creation_id': car}, 'POST')['id']
    link = call(f'/{pub}', {'fields': 'permalink'}).get('permalink', '')
    # Отметку пишем до ответа: если ответ упадёт, пост не выйдет второй раз
    done[pid] = {'post': pub, 'permalink': link, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    print('опубликовано:', link)
    if post.get('reply'):
        rc = call(f'/{me}/threads', {'media_type': 'TEXT', 'text': post['reply'], 'reply_to_id': pub}, 'POST')['id']
        wait_ready(rc)
        rid = call(f'/{me}/threads_publish', {'creation_id': rc}, 'POST')['id']
        done[pid]['reply'] = rid; STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
        print('ответ в ветке:', rid)

if __name__ == '__main__':
    main()
