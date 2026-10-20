#!/usr/bin/env python3
"""Narra con voces naturales los capítulos de Lámpara (https://lampara-biblia.vercel.app).

Para cada capítulo y cada voz deja:
  <voz>/<id>.m4a   audio (HE-AAC 24 kbps, mono)
  t/<id>.json      {"h": huella del texto, "seg": [frases tal como las muestra la página],
                    "v": {voz: {"d": duración, "s": [segundo en que empieza cada frase]}}}

Las frases salen igual que en setupSpeech() de index.html (mismo orden y mismos cortes), así la página
resalta la frase que se está oyendo. Si se cambia esa función, hay que cambiar también items() y frases().

Uso: python3 narrar.py [--solo genesis-1,mateo-6] [--datos carpeta/con/chapters.json] [--revisar]
     --revisar solo cuenta lo que falta (y lo deja en $GITHUB_OUTPUT como pendientes=N).
Necesita edge-tts 7.3.1 (o más nueva) y afconvert (macOS).
"""
import argparse, asyncio, hashlib, json, os, re, subprocess, sys, tempfile, urllib.request

try:
    import edge_tts  # 7.3.1 o más nueva: usa el pase "v2" que Microsoft pide desde octubre de 2026
    import edge_tts.communicate
    # Microsoft tiene dos servidores de narración y a veces uno rechaza (403/429) según el país o la versión:
    # cada reintento usa el otro.
    SERVIDORES = [edge_tts.communicate.WSS_URL,
                  'wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1'
                  '?Ocp-Apim-Subscription-Key=' + edge_tts.constants.TRUSTED_CLIENT_TOKEN]
except ImportError:  # --revisar no lo necesita
    edge_tts = None

SITIO = 'https://lampara-biblia.vercel.app'
VOCES = {
    'andrew': 'en-US-AndrewMultilingualNeural',
    'ava': 'en-US-AvaMultilingualNeural',
    'dalia': 'es-MX-DaliaNeural',
    'jorge': 'es-MX-JorgeNeural',
}
VERSION = 1  # subirla obliga a narrar todo otra vez (por ejemplo si cambia hablar())
AQUI = os.path.dirname(os.path.abspath(__file__))

# Emojis: aproximación de \p{Extended_Pictographic} más los modificadores (︎ ️ ‍ ⃣ tonos de piel).
EMO = re.compile('[©®‼⁉™ℹ↔-↙↩↪⌚⌛⌨⏏'
                 '⏩-⏳⏸-⏺Ⓜ▪▫▶◀◻-◾☀-➿'
                 '⤴⤵⬅-⬇⬛⬜⭐⭕〰〽㊗㊙'
                 '\U0001F000-\U0001FAFF\U0001FC00-\U0001FFFD︎️‍⃣]')


# stripEmoji() de index.html quita los emojis pero deja ⃣ y ︎ (por eso "1️⃣ Título" se ve como "1⃣ Título").
# Tampoco quita las banderas (letras regionales 🇦-🇿).
EMO_JS = re.compile(EMO.pattern.replace('\ufe0e', '').replace('\u20e3', '')
                    .replace('\U0001F000-\U0001FAFF', '\U0001F000-\U0001F1E5\U0001F200-\U0001FAFF'))


def sin_emoji(s):
    """stripEmoji() de index.html."""
    s = re.sub(r'^(?:' + EMO_JS.pattern + r'|\s)+', '', s)
    return EMO_JS.sub('', s).strip()


def u16(s):
    return len(s.encode('utf-16-le')) // 2  # largo como lo cuenta JavaScript


def items(c, ex):
    """Textos de los elementos [data-say] del capítulo, en orden (como su innerText)."""
    out = [c['title']]
    for s in c['sections']:
        h = sin_emoji(s.get('h') or '')
        if h:
            out.append(h)
        for tag, v in s['b']:
            if tag in ('p', 'q'):
                out.append(v)
            elif tag in ('ul', 'ol'):
                out.extend(v)
    q = (ex or {}).get('quote') or c.get('quote')
    if not q:
        for s in c['sections']:
            for tag, v in s['b']:
                if tag == 'q' and u16(v) < 200:
                    q = {'t': re.sub(r'^[“"]|[”"]\.?$', '', v)}
                    break
            if q:
                break
    if q:
        out.append('“' + re.sub(r'^[“"]|[”"]$', '', q['t']) + '”')
    return out


def frases(textos):
    """Mismos cortes que setupSpeech(): oraciones unidas mientras sumen menos de 180 caracteres."""
    segs = []
    for t in textos:
        t = re.sub(r'\s+', ' ', t).strip()
        if not t:
            continue
        acc = []
        for s in re.findall(r'[^.!?…:;]+[.!?…:;]*["”»]?\s*', t) or [t]:
            if acc and u16(acc[-1] + s) < 180:
                acc[-1] += s
            else:
                acc.append(s)
        segs.extend(x.strip() for x in acc)
    return segs


def hablar(t):
    """Texto que se le da a la voz: sin emojis ni símbolos que se leerían raro."""
    t = EMO.sub('', t)
    t = re.sub(r'\bvv?\.\s*(\d+)\s*[–-]\s*(\d+)', r'versículos \1 al \2', t)
    t = re.sub(r'\bvv\.\s*(\d+)', r'versículos \1', t)
    t = re.sub(r'\bv\.\s*(\d+)', r'versículo \1', t)
    t = re.sub(r'(\d+):(\d+)\s*[–-]\s*(\d+)', r'\1, versículos \2 al \3', t)
    t = re.sub(r'(\d+):(\d+)', r'\1, versículo \2', t)
    t = re.sub(r'(\d+)\s*[–-]\s*(\d+)', r'\1 al \2', t)
    t = t.replace('→', ', ').replace(' = ', ' es ').replace(' + ', ' más ').replace('•', '')
    t = re.sub(r'\s+([,.;:])', r'\1', re.sub(r'\s+', ' ', t)).strip(' ,')
    if t and t[-1] not in '.!?…:;"”»)':
        t += '.'  # pausa después de títulos
    return t


def palabras(t):
    return re.findall(r'\w+', t.lower())


async def narrar(texto_segs, voz, destino):
    """Narra todas las frases en una sola pasada y calcula en qué segundo empieza cada una."""
    habla = [hablar(s) for s in texto_segs]
    com = edge_tts.Communicate('\n'.join(h for h in habla if h), VOCES[voz], boundary='WordBoundary')
    audio, marcas = bytearray(), []
    async for ch in com.stream():
        if ch['type'] == 'audio':
            audio += ch['data']
        elif ch['type'] == 'WordBoundary':
            marcas.append((ch['offset'] / 1e7, ch['text']))
    # Alinear: cada palabra que dijo la voz avanza sobre las palabras de las frases.
    plano = [(k, w) for k, h in enumerate(habla) for w in palabras(h)]
    inicio, p = [None] * len(habla), 0
    for seg, txt in marcas:
        for w in palabras(txt):
            for j in range(p, min(p + 8, len(plano))):
                if plano[j][1] == w:
                    k = plano[j][0]
                    if inicio[k] is None:
                        inicio[k] = seg
                    p = j + 1
                    break
    with tempfile.TemporaryDirectory() as tmp:
        mp3 = os.path.join(tmp, 'a.mp3')
        open(mp3, 'wb').write(audio)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        subprocess.run(['afconvert', '-f', 'm4af', '-d', 'aach', '-b', '24000', mp3, destino], check=True)
    dur = float(re.search(r'estimated duration: ([\d.]+)', subprocess.run(
        ['afinfo', destino], capture_output=True, text=True).stdout).group(1))
    faltan = sum(1 for k, x in enumerate(inicio) if x is None and palabras(habla[k]))
    # Frases sin palabras alineadas: empiezan donde termina la anterior.
    prev = 0.0
    for k in range(len(inicio)):
        if inicio[k] is None or inicio[k] < prev:
            inicio[k] = prev
        prev = inicio[k]
    return {'d': round(dur, 2), 's': [round(x, 2) for x in inicio]}, faltan


def cargar(datos, nombre):
    if datos:
        return json.load(open(os.path.join(datos, nombre)))
    with urllib.request.urlopen(f'{SITIO}/data/{nombre}', timeout=60) as r:
        return json.load(r)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--solo', default='')
    ap.add_argument('--datos', default='')
    ap.add_argument('--hilos', type=int, default=4)
    ap.add_argument('--revisar', action='store_true')
    a = ap.parse_args()
    caps = cargar(a.datos, 'chapters.json')
    extras = cargar(a.datos, 'extras.json')
    solo = set(filter(None, a.solo.split(',')))
    trabajos, fichas = [], {}
    for c in caps:
        if solo and c['id'] not in solo:
            continue
        segs = frases(items(c, extras.get(c['id'])))
        huella = hashlib.sha1(json.dumps([VERSION, [hablar(s) for s in segs]], ensure_ascii=False).encode()).hexdigest()[:12]
        ruta = os.path.join(AQUI, 't', c['id'] + '.json')
        viejo = json.load(open(ruta)) if os.path.exists(ruta) else {}
        ficha = viejo if viejo.get('h') == huella else {'h': huella, 'seg': segs, 'v': {}}
        fichas[c['id']] = (ruta, ficha)
        for v in VOCES:
            if v not in ficha['v'] or not os.path.exists(os.path.join(AQUI, v, c['id'] + '.m4a')):
                trabajos.append((c['id'], v, segs))
    print(f'{len(trabajos)} audios por narrar', flush=True)
    if a.revisar:
        if os.environ.get('GITHUB_OUTPUT'):
            open(os.environ['GITHUB_OUTPUT'], 'a').write(f'pendientes={len(trabajos)}\n')
        return
    sem = asyncio.Semaphore(a.hilos)
    errores = []

    async def uno(cid, v, segs):
        async with sem:
            for intento in range(4):
                edge_tts.communicate.WSS_URL = SERVIDORES[intento % 2]
                try:
                    r, faltan = await narrar(segs, v, os.path.join(AQUI, v, cid + '.m4a'))
                    fichas[cid][1]['v'][v] = r
                    ruta, ficha = fichas[cid]
                    os.makedirs(os.path.dirname(ruta), exist_ok=True)
                    json.dump(ficha, open(ruta, 'w'), ensure_ascii=False, separators=(',', ':'))
                    print(f'  {cid} {v}: {r["d"]:.0f} s, {len(segs)} frases' + (f', {faltan} sin alinear' if faltan else ''), flush=True)
                    return
                except Exception as e:  # la conexión con Microsoft falla a veces: reintentar
                    err = e  # si pide esperar (429, demasiadas solicitudes), esperar más
                    await asyncio.sleep((30 if '429' in str(e) else 5) * (intento + 1))
            errores.append(f'{cid} {v}: {err}')
            print(f'  ERROR {cid} {v}: {err}', flush=True)

    await asyncio.gather(*(uno(*t) for t in trabajos))
    # Índice con lo que ya está narrado completo (las 4 voces)
    listos = {}
    for f in sorted(os.listdir(os.path.join(AQUI, 't'))) if os.path.isdir(os.path.join(AQUI, 't')) else []:
        d = json.load(open(os.path.join(AQUI, 't', f)))
        if all(v in d['v'] for v in VOCES):
            listos[f[:-5]] = d['h']
    json.dump({'voces': list(VOCES), 'capitulos': listos}, open(os.path.join(AQUI, 'indice.json'), 'w'), separators=(',', ':'))
    if errores:
        print(f'{len(errores)} errores', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
