import { h } from 'vue'
import DefaultTheme from 'vitepress/theme'
import './styles.css'
import CardLibrary from './components/CardLibrary.vue'
import EspInstallButton from './components/EspInstallButton.vue'
import EspInstallSelector from './components/EspInstallSelector.vue'
import GitHubStars from './components/GitHubStars.vue'
import IconGallery from './components/IconGallery.vue'

export default {
  extends: DefaultTheme,
  Layout() {
    return h(DefaultTheme.Layout, null, {
      'nav-bar-content-after': () => h(GitHubStars),
    })
  },
  enhanceApp({ app }) {
    app.component('CardLibrary', CardLibrary)
    app.component('EspInstallButton', EspInstallButton)
    app.component('EspInstallSelector', EspInstallSelector)
    app.component('GitHubStars', GitHubStars)
    app.component('IconGallery', IconGallery)
  },
}
