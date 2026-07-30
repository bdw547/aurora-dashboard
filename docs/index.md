---
layout: page
title: Aurora — a hand-crafted Home Assistant touch dashboard
titleTemplate: :title
description: "Hand-crafted ESPHome + LVGL firmware for the Guition 7″ ESP32-P4 panel: dark glass UI, Spotify, live camera, TV remote — designed in a no-code web configurator."
sidebar: false
---

<script setup>
import { withBase } from 'vitepress'

const strip = [
  { img: '0102_spotify_3x2.png', alt: 'Spotify now-playing card' },
  { img: '0054_climate_3x2.png', alt: 'Climate card' },
  { img: '0241_notifications_3x2.png', alt: 'Notifications card' },
  { img: '0075_speakers_3x2.png', alt: 'Multi-room speakers card with volume sliders' },
  { img: '0265_wx_current_3x2.png', alt: 'Current weather card' },
  { img: '0225_lock_2x2.png', alt: 'Door lock card' },
  { img: '0177_tvremote_2x2.png', alt: 'TV remote card' },
  { img: '0002_light_3x1.png', alt: 'Light card with dimmer' },
]

const features = [
  { t: 'Live camera + wake-on-approach', d: 'The onboard camera hardware-encodes H.264 and streams RTSP to Home Assistant. At night, the same frames wake the panel when you walk toward it.' },
  { t: 'Spotify, done right', d: 'Now-playing with album art, a speaker picker for every Spotify Connect device, and a browsable playlist → track library. Tap a song, pick a room, it plays.' },
  { t: 'A real TV remote', d: 'Full LG webOS control — D-pad, volume, apps — plus a Magic-Remote trackpad page with a genuine drag-to-move cursor and scroll wheel.' },
  { t: 'Designed in your browser', d: 'A no-code configurator maps every control to your own Home Assistant entities and lays out screens by drag-and-drop, with a pixel-exact live preview.' },
  { t: 'Rooms that follow your home', d: 'Rooms are data-driven — add, rename, and reassign rooms and their lights, fans, and switches without touching a config file.' },
  { t: 'One cable, once', d: 'Flash over USB a single time. Every update after that is wireless, and a photo screensaver with clock + temperature keeps the glass useful between touches.' },
]

const screens = [
  { img: 'controls', cap: 'Room controls — lights, fans, switches & blinds, one tap away' },
  { img: 'dashboard', cap: 'Home at a glance — locks, sensors & every light in one board' },
  { img: 'tv-remote', cap: 'LG webOS remote — D-pad, transport & app shortcuts' },
  { img: 'media', cap: 'Spotify library — browse playlists, tap to play in any room' },
  { img: 'trackpad', cap: 'Magic-Remote trackpad — a real cursor, scroll & volume' },
  { img: 'settings', cap: 'On-device settings — brightness, timeout & wake-on-approach' },
]
</script>

<div class="av">

<section class="av-hero">
  <div class="av-glow" aria-hidden="true"></div>
  <div class="av-hero-inner">
    <p class="av-eyebrow">ESPHome + LVGL firmware &nbsp;·&nbsp; Guition 7″ ESP32-P4</p>
    <h1 class="av-wordmark">Aurora</h1>
    <p class="av-thesis">
      A hand-crafted <strong>Home Assistant</strong> touch dashboard — dark glass, real pixels,
      and a no-code configurator that makes every screen yours.
    </p>
    <div class="av-cta">
      <a class="av-btn av-btn-solid" :href="withBase('/setup/')">Set up your panel</a>
      <a class="av-btn av-btn-ghost" :href="withBase('/cards/')">Browse the card library</a>
    </div>
    <img
      class="av-panel"
      :src="withBase('/images/aurora-panel.png')"
      alt="The Aurora weather screen on the 7-inch panel: hourly and 6-day forecast, humidity, wind and sunrise"
      width="960"
      fetchpriority="high"
    />
  </div>
</section>

<section class="av-section av-strip-wrap">
  <p class="av-eyebrow">Real firmware, real pixels</p>
  <p class="av-lede">
    Nothing on this site is a mockup. Every card is rendered by the same LVGL code that runs on
    the panel — <a :href="withBase('/cards/')">all 53 card types, in every size</a>.
  </p>
  <div class="av-strip">
    <img
      v-for="c in strip"
      :key="c.img"
      :src="withBase(`/cards/${c.img}`)"
      :alt="c.alt"
      loading="lazy"
      decoding="async"
    />
  </div>
</section>

<section class="av-section">
  <p class="av-eyebrow">What's inside</p>
  <div class="av-features">
    <article v-for="f in features" :key="f.t" class="av-feature">
      <h3>{{ f.t }}</h3>
      <p>{{ f.d }}</p>
    </article>
  </div>
</section>

<section class="av-section">
  <p class="av-eyebrow">A tour of the panel</p>
  <div class="av-tour">
    <figure v-for="s in screens" :key="s.img">
      <img :src="withBase(`/images/tour/${s.img}.png`)" :alt="s.cap" loading="lazy" decoding="async" />
      <figcaption>{{ s.cap }}</figcaption>
    </figure>
  </div>
</section>

<section class="av-section av-conf">
  <div class="av-conf-copy">
    <p class="av-eyebrow">No YAML required</p>
    <h2>Design it your way</h2>
    <p>
      The web configurator runs on your own machine. Point it at Home Assistant, map each card to
      your entities, arrange pages on a 6×5 grid with a live preview that matches the panel
      pixel-for-pixel — then press <strong>Flash</strong>.
    </p>
    <a class="av-btn av-btn-solid" :href="withBase('/setup/configurator')">See how it works</a>
  </div>
  <img
    class="av-conf-shot"
    src="./marketing/configurator.png"
    alt="The Aurora web configurator: card palette, live device preview and entity inspector"
    loading="lazy"
    decoding="async"
  />
</section>

<section class="av-section av-end">
  <h2>Runs on a $60 panel.</h2>
  <p>
    Guition JC1060P470C — ESP32-P4, 7″ 1024×600 IPS, capacitive touch, onboard camera. One USB-C
    cable for the first flash; wireless forever after.
  </p>
  <div class="av-cta">
    <a class="av-btn av-btn-solid" :href="withBase('/setup/')">Get started</a>
    <a class="av-btn av-btn-ghost" href="https://github.com/bdw547/aurora-dashboard">GitHub</a>
  </div>
  <p class="av-credit">
    Aurora began as a fork of
    <a href="https://github.com/jtenniswood/espcontrol">jtenniswood/espcontrol</a> and reuses its
    hardware bring-up; the dashboard is an independent rewrite. PolyForm Noncommercial license.
  </p>
</section>

</div>

<style>
.av {
  --av-teal: #2ed5b8;
  --av-violet: #b06cff;
  overflow-x: clip;
  font-family: var(--vp-font-family-base);
}
.av .av-eyebrow {
  font-family: var(--vp-font-family-utility);
  font-size: 12px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--av-teal);
  margin: 0 0 14px;
}

/* ——— hero ——— */
.av-hero {
  position: relative;
  padding: calc(var(--vp-nav-height) + 48px) 24px 0;
  text-align: center;
}
.av-glow {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(560px 420px at 30% -60px, rgba(46, 213, 184, 0.16), transparent 70%),
    radial-gradient(640px 460px at 72% 40px, rgba(176, 108, 255, 0.13), transparent 70%);
}
.av-hero-inner {
  position: relative;
  max-width: 1040px;
  margin: 0 auto;
}
.av-wordmark {
  font-family: var(--vp-font-family-display);
  font-size: clamp(64px, 12vw, 128px);
  font-weight: 700;
  letter-spacing: -0.04em;
  line-height: 1;
  margin: 0;
  background: linear-gradient(92deg, var(--av-teal) 15%, var(--av-violet) 85%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.av-thesis {
  max-width: 56ch;
  margin: 20px auto 0;
  font-size: 18px;
  line-height: 1.6;
  color: var(--vp-c-text-2);
}
.av-thesis strong {
  color: var(--vp-c-text-1);
}
.av-cta {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
  margin-top: 28px;
}
.av-btn {
  display: inline-block;
  padding: 10px 22px;
  border-radius: 10px;
  font-size: 14.5px;
  font-weight: 700;
  text-decoration: none;
  transition: background-color 0.2s, border-color 0.2s, color 0.2s;
}
.av-btn-solid {
  background: var(--av-teal);
  color: #06231d;
}
.av-btn-solid:hover {
  background: #4be0c6;
  color: #06231d;
}
.av-btn-ghost {
  border: 1px solid var(--vp-c-divider);
  color: var(--vp-c-text-1);
}
.av-btn-ghost:hover {
  border-color: var(--av-teal);
  color: var(--av-teal);
}
.av-panel {
  display: block;
  width: min(100%, 960px);
  margin: 52px auto 0;
  border-radius: 18px;
  border: 1px solid var(--vp-c-divider);
  box-shadow:
    0 40px 90px -30px rgba(0, 0, 0, 0.8),
    0 0 120px -20px rgba(46, 213, 184, 0.14);
}

/* ——— shared section shell ——— */
.av-section {
  max-width: 1040px;
  margin: 0 auto;
  padding: 88px 24px 0;
}
.av-lede {
  max-width: 58ch;
  color: var(--vp-c-text-2);
  margin: 0 0 26px;
}
.av-lede a {
  color: var(--av-teal);
  text-decoration: underline;
  text-underline-offset: 3px;
}

/* ——— card strip ——— */
.av-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 14px;
}
.av-strip img {
  height: auto;
  max-width: 100%;
  border-radius: 12px;
  border: 1px solid var(--vp-c-divider);
  flex: 0 1 auto;
}
.av-strip img[src*='3x2'],
.av-strip img[src*='3x1'] {
  width: 300px;
}
.av-strip img[src*='2x2'] {
  width: 196px;
}

/* ——— features ——— */
.av-features {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 14px;
}
.av-feature {
  background: var(--vp-c-bg-soft);
  border: 1px solid var(--vp-c-divider);
  border-radius: 14px;
  padding: 22px;
}
.av-feature h3 {
  font-family: var(--vp-font-family-display);
  font-size: 16.5px;
  font-weight: 600;
  margin: 0 0 8px;
  border: none;
  padding: 0;
}
.av-feature p {
  font-size: 14px;
  line-height: 1.65;
  color: var(--vp-c-text-2);
  margin: 0;
}

/* ——— tour ——— */
.av-tour {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 18px;
}
.av-tour figure {
  margin: 0;
}
.av-tour img {
  width: 100%;
  height: auto;
  border-radius: 12px;
  border: 1px solid var(--vp-c-divider);
}
.av-tour figcaption {
  font-size: 12.5px;
  color: var(--vp-c-text-3);
  margin-top: 8px;
  line-height: 1.5;
}

/* ——— configurator band ——— */
.av-conf {
  display: grid;
  grid-template-columns: minmax(280px, 5fr) 7fr;
  gap: 40px;
  align-items: center;
}
.av-conf h2 {
  font-family: var(--vp-font-family-display);
  font-size: 30px;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0 0 14px;
  border: none;
  padding: 0;
}
.av-conf p {
  color: var(--vp-c-text-2);
  line-height: 1.65;
  margin: 0 0 22px;
}
.av-conf-shot {
  width: 100%;
  height: auto;
  border-radius: 14px;
  border: 1px solid var(--vp-c-divider);
  box-shadow: 0 30px 70px -30px rgba(0, 0, 0, 0.7);
}
@media (max-width: 860px) {
  .av-conf {
    grid-template-columns: 1fr;
  }
}

/* ——— end band ——— */
.av-end {
  text-align: center;
  padding-bottom: 96px;
}
.av-end h2 {
  font-family: var(--vp-font-family-display);
  font-size: clamp(28px, 5vw, 40px);
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0 0 14px;
  border: none;
  padding: 0;
}
.av-end > p {
  max-width: 52ch;
  margin: 0 auto;
  color: var(--vp-c-text-2);
  line-height: 1.65;
}
.av-credit {
  margin-top: 56px !important;
  font-size: 12.5px;
  color: var(--vp-c-text-3);
}
.av-credit a {
  color: var(--vp-c-text-2);
  text-decoration: underline;
  text-underline-offset: 3px;
}

@media (prefers-reduced-motion: no-preference) {
  .av-hero-inner > * {
    animation: av-rise 0.7s cubic-bezier(0.16, 1, 0.3, 1) both;
  }
  .av-hero-inner > :nth-child(2) { animation-delay: 0.06s; }
  .av-hero-inner > :nth-child(3) { animation-delay: 0.12s; }
  .av-hero-inner > :nth-child(4) { animation-delay: 0.18s; }
  .av-hero-inner > :nth-child(5) { animation-delay: 0.26s; }
  @keyframes av-rise {
    from {
      opacity: 0;
      transform: translateY(14px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
}
</style>
