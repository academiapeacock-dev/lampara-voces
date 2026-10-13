# Voces de Lámpara

Audios con voces naturales de los capítulos de [Lámpara](https://lampara-biblia.vercel.app), la Biblia explicada fácil.

- `andrew/`, `ava/`, `dalia/`, `jorge/`: un audio por capítulo (`<id>.m4a`, HE-AAC 24 kbps mono).
- `t/<id>.json`: las frases del capítulo y el segundo en que empieza cada una en cada voz (para resaltar lo que se oye).
- `muestra/<voz>.m4a`: la frase de prueba del menú de voces.
- `indice.json`: capítulos narrados con las cuatro voces.

Se publican con GitHub Pages en https://academiapeacock-dev.github.io/lampara-voces/

## Cómo se actualiza

El robot `.github/workflows/narrar.yml` revisa la página cada 10 minutos. Si hay un capítulo nuevo, o si cambió el texto
de uno ya narrado, lo narra con `narrar.py` (voces de Microsoft Edge vía `edge-tts`) y sube los audios.
Para narrar ya mismo: pestaña **Actions** → **Narrar capítulos** → **Run workflow**.

`narrar.py` separa las frases igual que `setupSpeech()` en `index.html` de Lámpara. Si se cambia una, hay que cambiar
la otra: la página solo usa estos audios cuando sus frases coinciden exactamente; si no, lee con la voz del aparato.
