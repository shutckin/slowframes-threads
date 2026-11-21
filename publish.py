#!/usr/bin/env python3
"""Публикует очередной пост из очереди в личный Threads (@shoot.kin).

Схема как у EastRide (tripride/scripts/social-daily.ts), только на Python
без зависимостей: очередь и кадры лежат в этом репозитории, GitHub Actions
запускает скрипт по расписанию, картинки Threads забирает по raw-ссылкам
GitHub. Локальная машина для публикации не нужна.

Очередей две, аккаунт один:
  slowframes — queue/<id>/post.json + 1.jpg … 5.jpg, карусель кадров;
               {"hook": "...", "reply": "...", "images": ["1.jpg", ...]}
  atlas      — queue-atlas/<id>/post.json, посты LATAM Atlas; ссылка на сайт —
               в первом ответе ("reply"). Два вида:
               {"text": "...", "reply": "..."} — просто текст;
               {"text": "...", "reply": "...", "images": ["card.png"]} — текст
               с картинкой (карточка сравнения с сайта). Одна картинка уходит
               одиночным постом IMAGE, две и больше — каруселью, как slowframes.
  lichnoe    — queue-lichnoe/<id>/post.json, личные посты Артема с его «Беру» на столе;
               {"text": "...", "reply": "...", "topic": "Вайбкодинг"}, один в день.
Ветка-история (21.11.2026): необязательное поле "thread": ["...", "..."] — сообщения, которые выходят
цепочкой под постом, каждое ответом на предыдущее (1-10 штук, каждое до 500 знаков). "reply", если есть,
встаёт последним, ответом на конец цепочки.
Подпись поста — "text", а если его нет — "hook" (так устроены посты
slowframes). Картинки — JPEG или PNG (официально поддерживаемые Threads
форматы), до 8 МБ каждая, в карусели от 2 до 20; файлы должны лежать в
папке поста. Это проверяется до отправки, в том числе холостым прогоном.
Порядок — по имени папки (числовой префикс). У каждой очереди свой файл
в state/, он помнит, что уже вышло; повтор невозможен: отметка пишется
сразу после публикации.

Ритм у каждой очереди свой (days в QUEUES): slowframes — раз в три дня,
атлас — тоже раз в три дня (с 01.11.2026: с 01.10 выходил ежедневно, охват
последних постов упал до 130-320 просмотров, Артем решил реже, но сильнее),
и не в день slowframes: посты чередуются, а не выходят парой. MIN_INTERVAL_DAYS в окружении перебивает оба. Расписание в
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
  python3 publish.py --queue atlas --dry --dir queue-atlas-draft --id <id>
                                      # проверить черновик из другой папки
                                      # (--dir работает только с --dry)
Холостому прогону токен не нужен: он ничего не отправляет и токен не читает.
"""
import calendar, json, os, sys, time, pathlib, urllib.request, urllib.parse, urllib.error

BASE = 'https://graph.threads.net/v1.0'
HERE = pathlib.Path(__file__).parent
RAW = os.environ.get('RAW_BASE', 'https://raw.githubusercontent.com/shutckin/slowframes-threads/main')
TOKEN = os.environ.get('THREADS_RU_ACCESS_TOKEN', '')
DRY = '--dry' in sys.argv
FORCE = '--force' in sys.argv or '--id' in sys.argv
SLACK_DAYS = 0.25
MAX_CHARS = 500

QUEUES = {
    'slowframes': {'dir': 'queue', 'state': 'state/published.json', 'days': 3},
    # avoid — не публиковать, если другая очередь выходила меньше AVOID_DAYS назад
    'atlas': {'dir': 'queue-atlas', 'state': 'state/published-atlas.json', 'avoid': 'slowframes', 'days': 3},
    # Посты из мыслей Артема (робот «мысль → ролик», 08.11.2026): выходят сразу после его «да» в боте,
    # бот кладёт пост в queue-mysli и пушит, а пуш в эту папку запускает публикацию (daily.yml, on: push).
    'mysli': {'dir': 'queue-mysli', 'state': 'state/published-mysli.json', 'days': 0},
    # Личные посты Артема (15.11.2026): черновики пишутся на столе 15-Threads в проекте Youtube DJIYVLOM, сюда
    # попадают только те, на которые Артем нажал «Беру» и «Поставить в очередь». Один пост в день, свой запуск
    # по расписанию в 06:00 UTC (daily.yml), поэтому с остальными очередями в один час не сталкивается.
    'lichnoe': {'dir': 'queue-lichnoe', 'state': 'state/published-lichnoe.json', 'days': 1},
}
AVOID_DAYS = 0.5
# Второй слот (решение Артема 15.11.2026: «оставить, но адаптировать под новую выкладку»): главный пост дня личный
# (очередь lichnoe, 06:00 UTC), а slowframes, атлас и посты EastRide делят второй слот, не больше одного в сутки.
# EastRide публикует сам из своего репозитория, поэтому смотрим на сам аккаунт: если за SECOND_SLOT_HOURS часов уже
# вышел пост не из личной очереди, ждём завтра. Ответы в ветках в /me/threads не попадают.
SECOND_SLOT = {'slowframes', 'atlas'}
SECOND_SLOT_HOURS = 20

def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

QUEUE = arg('--queue', 'slowframes')
if QUEUE not in QUEUES: raise SystemExit(f'неизвестная очередь {QUEUE}: {", ".join(QUEUES)}')
CFG = QUEUES[QUEUE]
# --dir — только для проверки черновиков: публиковать из чужой папки нельзя,
# иначе пост выйдет мимо очереди и его ссылки на картинки не совпадут с репозиторием
QDIR_NAME = arg('--dir', CFG['dir'])
if QDIR_NAME != CFG['dir'] and not DRY: raise SystemExit('--dir работает только вместе с --dry')
QDIR = HERE/QDIR_NAME
STATE = HERE/CFG['state']
MIN_DAYS = float(os.environ.get('MIN_INTERVAL_DAYS') or CFG['days'])

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

def wait_ready(cid, video=False):
    # Threads готовит контейнер асинхронно; на неготовый отвечает так, будто
    # его нет. Карусель из пяти кадров может собираться около минуты, видео дольше.
    for delay in (3, 5, 10, 20, 30, 30) + ((30,) * 10 if video else ()):
        time.sleep(delay)
        j = call(f'/{cid}', {'fields': 'status,error_message'})
        if j.get('status') == 'FINISHED': return
        if j.get('status') == 'ERROR': raise SystemExit(f"контейнер {cid} отклонён: {j.get('error_message')}")
    raise SystemExit(f'контейнер {cid} не готов после {98 + (300 if video else 0)} секунд')

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
    if QUEUE in SECOND_SLOT and not DRY:
        busy = second_slot_taken()
        if busy: return False, busy
    return True, ''

def second_slot_taken():
    """Вышел ли за последние часы пост не из личной очереди (EastRide, атлас, slowframes, мысли)."""
    personal = {v.get('permalink') for v in load(HERE/QUEUES['lichnoe']['state']).values()}
    recent = call('/me/threads', {'fields': 'timestamp,permalink,media_type', 'limit': 10}).get('data', [])
    for p in recent:
        if p.get('media_type') == 'REPOST_FACADE' or p.get('permalink') in personal: continue
        at = calendar.timegm(time.strptime(p['timestamp'][:19], '%Y-%m-%dT%H:%M:%S'))
        hours = (time.time() - at) / 3600
        if hours < SECOND_SLOT_HOURS:
            return f'второй слот занят: {hours:.0f} ч назад вышел другой пост ({p.get("permalink", "")}), ждём завтра'
    return ''

MAX_THREAD = 10                          # сообщений в ветке-истории под постом

def check_length(post):
    for field in ('text', 'hook', 'reply'):
        n = len(post.get(field) or '')
        if n > MAX_CHARS: raise SystemExit(f'поле {field}: {n} знаков, лимит Threads {MAX_CHARS}')
    thread = post.get('thread')
    if thread is None: return
    if not isinstance(thread, list) or not thread or not all(isinstance(t, str) and t.strip() for t in thread):
        raise SystemExit('поле thread: нужен список непустых сообщений')
    if len(thread) > MAX_THREAD: raise SystemExit(f'в ветке {len(thread)} сообщений, держим до {MAX_THREAD}')
    for k, t in enumerate(thread, 1):
        if len(t) > MAX_CHARS: raise SystemExit(f'сообщение ветки {k}: {len(t)} знаков, лимит Threads {MAX_CHARS}')

IMAGE_TYPES = ('.jpg', '.jpeg', '.png')  # официально поддерживаемые Threads форматы
MAX_IMAGE_BYTES = 8 * 1024 * 1024        # лимит Threads на картинку
MAX_CAROUSEL = 20                        # в карусели от 2 до 20 кадров

def check_images(folder, images):
    """Картинки есть в папке поста, формат и размер подходят Threads."""
    if not isinstance(images, list) or not all(isinstance(n, str) and n for n in images):
        raise SystemExit('поле images: нужен список имён файлов, например ["card.png"]')
    if len(images) > MAX_CAROUSEL: raise SystemExit(f'картинок {len(images)}, в карусели Threads не больше {MAX_CAROUSEL}')
    for name in images:
        f = folder/name
        if '/' in name or not f.is_file(): raise SystemExit(f'нет файла картинки {name} в папке {folder.name}')
        if f.suffix.lower() not in IMAGE_TYPES: raise SystemExit(f'{name}: Threads принимает только JPEG и PNG')
        if f.stat().st_size > MAX_IMAGE_BYTES: raise SystemExit(f'{name}: {f.stat().st_size // 1024} КБ, лимит Threads 8 МБ')

VIDEO_TYPES = ('.mp4', '.mov')
MAX_VIDEO_BYTES = 90 * 1024 * 1024        # Threads берёт до 1 ГБ, но GitHub не хранит файлы больше 100 МБ

def check_video(folder, name):
    """Видео поста (с 15.11.2026): один файл MP4/MOV в папке поста, Threads забирает его по raw-ссылке."""
    f = folder/name
    if '/' in name or not f.is_file(): raise SystemExit(f'нет видео {name} в папке {folder.name}')
    if f.suffix.lower() not in VIDEO_TYPES: raise SystemExit(f'{name}: видео только MP4 или MOV')
    if f.stat().st_size > MAX_VIDEO_BYTES: raise SystemExit(f'{name}: {f.stat().st_size // 2**20} МБ, держим до 90 МБ')

def media_kind(images, video=''):
    if video: return 'VIDEO'
    return 'CAROUSEL' if len(images) > 1 else 'IMAGE' if images else 'TEXT'

def create_container(me, params):
    """Контейнер поста. Метку темы (topic_tag) Threads может не принять: тогда тот же пост без метки.
    Повтор безопасен: упал сам запрос создания, значит, ничего не создано и не опубликовано."""
    try:
        return call(f'/{me}/threads', params, 'POST')['id']
    except SystemExit as e:
        if 'topic_tag' not in params: raise
        print(f'метку темы «{params["topic_tag"]}» Threads не принял ({e}), публикую без неё')
        return call(f'/{me}/threads', {k: v for k, v in params.items() if k != 'topic_tag'}, 'POST')['id']

def publish_container(me, params):
    cid = create_container(me, params)
    wait_ready(cid, video=params.get('media_type') == 'VIDEO')
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
    folder = QDIR/pid
    if not (folder/'post.json').exists(): raise SystemExit(f'нет поста {pid} в папке {QDIR_NAME}')
    post = json.loads((folder/'post.json').read_text())
    check_length(post)
    images = post.get('images') or []
    check_images(folder, images)
    video = str(post.get('video') or '')
    if video:
        if images: raise SystemExit(f'пост {pid}: видео и картинки вместе публикатор пока не умеет')
        check_video(folder, video)
    urls = [f'{RAW}/{QDIR_NAME}/{pid}/{name}' for name in (images or ([video] if video else []))]
    # Подпись: у атласа "text", у slowframes "hook"
    body = post.get('text') or post.get('hook') or ''
    if not body.strip(): raise SystemExit(f'пост {pid}: пустой текст (нужно поле text или hook)')
    kind = media_kind(images, video)
    # Метка темы поста (как «Вайб Кодинг» у тем в Threads): необязательное поле topic, 1-50 знаков без точек и &
    topic = str(post.get('topic') or '').strip()
    if topic and (len(topic) > 50 or any(ch in topic for ch in '.&')):
        raise SystemExit(f'пост {pid}: метка темы «{topic}» не подходит Threads (до 50 знаков, без точек и &)')
    extra = {'topic_tag': topic} if topic else {}
    thread = post.get('thread') or []
    print(f'{QUEUE} / пост {pid}: {kind}, картинок {len(urls)}' + (f', тема «{topic}»' if topic else '') +
          (f', в ветке {len(thread)} сообщ.' if thread else '') +
          ''.join(f'\n{body}' if i == 0 else f'\n--- сообщение {i + 1}\n{t}' for i, t in enumerate([body, *thread])) +
          f'\n---\n{post.get("reply", "")}')
    if DRY:
        for u in urls: print(' ', u)
        print(f'знаков: текст {len(body)}, ответ {len(post.get("reply") or "")}' +
              (f', ветка {[len(t) for t in thread]}' if thread else '') + f' (лимит {MAX_CHARS})')
        return
    if not TOKEN: raise SystemExit('THREADS_RU_ACCESS_TOKEN не задан')
    me = call('/me', {'fields': 'id'})['id']
    if kind == 'CAROUSEL':
        children = []
        for u in urls:
            c = call(f'/{me}/threads', {'media_type': 'IMAGE', 'image_url': u, 'is_carousel_item': 'true'}, 'POST')['id']
            wait_ready(c); children.append(c)
        pub = publish_container(me, {'media_type': 'CAROUSEL', 'children': ','.join(children), 'text': body, **extra})
    elif kind == 'VIDEO':
        pub = publish_container(me, {'media_type': 'VIDEO', 'video_url': urls[0], 'text': body, **extra})
    elif kind == 'IMAGE':
        pub = publish_container(me, {'media_type': 'IMAGE', 'image_url': urls[0], 'text': body, **extra})
    else:
        pub = publish_container(me, {'media_type': 'TEXT', 'text': body, **extra})
    link = call(f'/{pub}', {'fields': 'permalink'}).get('permalink', '')
    # Отметку пишем до ответа: если ответ упадёт, пост не выйдет второй раз
    done[pid] = {'post': pub, 'permalink': link, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    STATE.parent.mkdir(exist_ok=True); STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
    print('опубликовано:', link)
    last = publish_thread(me, pid, pub, thread, done)
    if post.get('reply'):
        rid = publish_container(me, {'media_type': 'TEXT', 'text': post['reply'], 'reply_to_id': last})
        done[pid]['reply'] = rid; STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
        print('ответ в ветке:', rid)

def publish_thread(me, pid, pub, thread, done):
    """Ветка-история: каждое сообщение ответом на предыдущее. Id каждого вышедшего пишется в state сразу:
    если цепочка оборвётся, видно, с какого сообщения доопубликовать руками (повтор поста запрещён)."""
    last = pub
    for k, text in enumerate(thread, 1):
        try:
            last = publish_container(me, {'media_type': 'TEXT', 'text': text, 'reply_to_id': last})
        except SystemExit as e:
            done[pid]['thread_stopped_at'] = k
            STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
            raise SystemExit(f'ветка {pid} оборвалась на ответе {k} из {len(thread)} (вышли пост и {k - 1} отв.): {e}')
        done[pid].setdefault('thread', []).append(last)
        STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1))
        print(f'ветка, ответ {k} из {len(thread)}:', last)
    return last

if __name__ == '__main__':
    main()
