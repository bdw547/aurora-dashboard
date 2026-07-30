<script setup lang="ts">
import { ref, computed } from 'vue'
import { withBase } from 'vitepress'
import data from '../../data/card-library.json'

const active = ref('')

const categories = computed(() =>
  data.map((c) => ({ ...c, id: c.category.toLowerCase().replace(/[^a-z0-9]+/g, '-') })),
)

const total = data.reduce(
  (n, c) => n + c.kinds.reduce((k, kind) => k + kind.cards.length, 0),
  0,
)
const kindCount = data.reduce((n, c) => n + c.kinds.length, 0)
</script>

<template>
  <div class="card-library">
    <p class="cl-intro">
      Every card below is a <strong>real firmware render</strong> — captured from the Aurora
      emulator, pixel-identical to the panel. {{ kindCount }} card types in {{ total }} grid
      sizes, all placeable on any page from the
      <a :href="withBase('/setup/configurator')">web configurator</a>.
    </p>

    <nav class="cl-chips" aria-label="Card categories">
      <a
        v-for="c in categories"
        :key="c.id"
        :href="`#${c.id}`"
        class="cl-chip"
        :class="{ active: active === c.id }"
        @click="active = c.id"
        >{{ c.category }}</a
      >
    </nav>

    <section v-for="c in categories" :key="c.id" class="cl-cat">
      <h2 :id="c.id" tabindex="-1">
        {{ c.category }}
        <a class="header-anchor" :href="`#${c.id}`" :aria-label="`Permalink to ${c.category}`">&ZeroWidthSpace;</a>
      </h2>
      <div v-for="kind in c.kinds" :key="kind.ck" class="cl-kind">
        <h3>
          {{ kind.label }}
          <span class="cl-count">{{ kind.cards.length }} size{{ kind.cards.length > 1 ? 's' : '' }}</span>
        </h3>
        <div class="cl-grid">
          <figure v-for="card in kind.cards" :key="card.img" class="cl-card">
            <img
              :src="withBase(`/cards/${card.img}`)"
              :alt="`${kind.label} card, ${card.w} columns by ${card.h} rows`"
              loading="lazy"
              decoding="async"
            />
            <figcaption>{{ card.w }}×{{ card.h }}</figcaption>
          </figure>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.cl-intro {
  max-width: 62ch;
}
.cl-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 20px 0 8px;
}
.cl-chip {
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.02em;
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid var(--vp-c-divider);
  color: var(--vp-c-text-2);
  text-decoration: none;
  transition:
    color 0.2s,
    border-color 0.2s;
}
.cl-chip:hover,
.cl-chip.active {
  color: var(--vp-c-brand-1);
  border-color: var(--vp-c-brand-1);
}
.cl-cat h2 {
  margin-top: 44px;
  border-top: 1px solid var(--vp-c-divider);
  padding-top: 22px;
}
.cl-kind h3 {
  margin: 26px 0 4px;
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.cl-count {
  font-size: 12px;
  font-weight: 500;
  color: var(--vp-c-text-3);
}
.cl-grid {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 14px;
  margin-top: 12px;
}
.cl-card {
  margin: 0;
}
.cl-card img {
  display: block;
  max-width: min(100%, 460px);
  height: auto;
  border-radius: 10px;
  border: 1px solid var(--vp-c-divider);
  background: #0b0c10;
}
.cl-card figcaption {
  font-size: 11.5px;
  color: var(--vp-c-text-3);
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}
</style>
