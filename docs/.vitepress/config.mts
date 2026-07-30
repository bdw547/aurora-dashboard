import { defineConfig } from 'vitepress'

const hostname = 'https://bdw547.github.io/aurora-dashboard/'
const defaultImage = {
  url: `${hostname}images/aurora-hero.png`,
  width: '1024',
  height: '640',
  type: 'image/png',
}

export default defineConfig({
  title: 'Aurora',
  description:
    'A hand-crafted Home Assistant touch dashboard for the Guition 7″ ESP32-P4 panel — ESPHome + LVGL firmware with a no-code web configurator.',
  base: '/aurora-dashboard/',
  lang: 'en-US',
  cleanUrls: true,
  lastUpdated: true,
  appearance: 'force-dark',
  ignoreDeadLinks: [/^https?:\/\/localhost/],

  // The upstream espcontrol documentation is intentionally kept on disk (the
  // repo's contract checks cross-reference it) but excluded from the site.
  srcExclude: [
    'card-types/**',
    'features/**',
    'generated/**',
    'getting-started/**',
    'reference/**',
    'screens/**',
  ],

  sitemap: {
    hostname,
    transformItems: (items) => items.filter((item) => item.url !== '404' && item.url !== '/404'),
  },

  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/aurora-dashboard/favicon.svg' }],
    ['link', { rel: 'preconnect', href: 'https://fonts.googleapis.com' }],
    ['link', { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' }],
    [
      'link',
      {
        rel: 'stylesheet',
        href: 'https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=JetBrains+Mono:wght@500&display=swap',
      },
    ],
    [
      'meta',
      {
        name: 'keywords',
        content:
          'Aurora, Home Assistant, ESPHome, LVGL, ESP32-P4, Guition, JC1060P470, touchscreen, dashboard, smart home panel',
      },
    ],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:locale', content: 'en_US' }],
    ['meta', { property: 'og:site_name', content: 'Aurora' }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    [
      'script',
      { type: 'application/ld+json' },
      JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'SoftwareApplication',
        '@id': `${hostname}#software`,
        name: 'Aurora',
        applicationCategory: 'UtilitiesApplication',
        operatingSystem: 'ESP32',
        description:
          'Hand-crafted Home Assistant touch dashboard firmware for the Guition 7-inch ESP32-P4 panel, with a no-code web configurator.',
        url: hostname,
        author: { '@type': 'Person', name: 'bdw547', url: 'https://github.com/bdw547' },
        offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
      }),
    ],
  ],

  transformPageData(pageData) {
    const canonicalUrl = `${hostname}${pageData.relativePath}`
      .replace(/index\.md$/, '')
      .replace(/\.md$/, '')

    const rawTitle = pageData.frontmatter.title ?? pageData.title
    const title =
      typeof rawTitle === 'string' ? rawTitle : rawTitle != null ? String(rawTitle) : ''
    const description = String(pageData.frontmatter.description ?? '')

    pageData.frontmatter.head ??= []
    pageData.frontmatter.head.push(
      ['link', { rel: 'canonical', href: canonicalUrl }],
      ['meta', { property: 'og:title', content: title }],
      ['meta', { property: 'og:description', content: description }],
      ['meta', { property: 'og:url', content: canonicalUrl }],
      ['meta', { property: 'og:image', content: defaultImage.url }],
      ['meta', { property: 'og:image:width', content: defaultImage.width }],
      ['meta', { property: 'og:image:height', content: defaultImage.height }],
      ['meta', { property: 'og:image:type', content: defaultImage.type }],
      ['meta', { name: 'twitter:title', content: title }],
      ['meta', { name: 'twitter:description', content: description }],
      ['meta', { name: 'twitter:image', content: defaultImage.url }],
    )

    if (pageData.relativePath === '404.md') {
      pageData.frontmatter.head.push(['meta', { name: 'robots', content: 'noindex' }])
    }
  },

  themeConfig: {
    nav: [
      { text: 'Setup', link: '/setup/' },
      { text: 'Card Library', link: '/cards/' },
      { text: 'GitHub', link: 'https://github.com/bdw547/aurora-dashboard' },
    ],

    sidebar: [
      {
        text: 'Setup',
        items: [
          { text: 'Setup guide', link: '/setup/' },
          { text: 'Web configurator', link: '/setup/configurator' },
          { text: 'Home Assistant packages', link: '/setup/home-assistant' },
        ],
      },
      {
        text: 'Reference',
        items: [{ text: 'Card library', link: '/cards/' }],
      },
    ],

    editLink: {
      pattern: 'https://github.com/bdw547/aurora-dashboard/edit/main/docs/:path',
      text: 'Edit this page on GitHub',
    },

    socialLinks: [{ icon: 'github', link: 'https://github.com/bdw547/aurora-dashboard' }],

    search: {
      provider: 'local',
    },
  },
})
