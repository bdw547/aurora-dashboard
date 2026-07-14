const components = [
  { name: "Lights", detail: "Toggle, dim, and group lights by room with live Home Assistant state." },
  { name: "Climate", detail: "Weather, temperature, humidity, wind, and thermostat-ready controls." },
  { name: "Security", detail: "Locks, presence, sensors, alerts, and the panel camera feed." },
  { name: "Media", detail: "Now playing, album art, queue, playlists, tracks, and Spotify Connect speakers." },
  { name: "TV remote", detail: "LG webOS D-pad, app shortcuts, transport keys, volume, and trackpad cursor." },
  { name: "Rooms", detail: "Generated room pages from a simple rooms wizard and entity assignments." },
  { name: "Notifications", detail: "Five-alert queue, severity levels, wake-up behavior, and safe action buttons." },
  { name: "Screensavers", detail: "Photo or Spotify screensaver modes with clock and weather overlays." },
  { name: "Cameras", detail: "RTSP camera cards plus wake-on-approach using the onboard camera." },
  { name: "Weather", detail: "Forecast-friendly cards for glanceable outdoor context." },
  { name: "Covers and fans", detail: "Blinds, covers, fans, switches, scenes, scripts, and quick actions." },
  { name: "Network", detail: "Panel Wi-Fi, device status, diagnostics, and uptime surfaces." },
];

const setupSteps = [
  "Connect Home Assistant with your local URL and token.",
  "Map Aurora slots to your own lights, rooms, media players, locks, and sensors.",
  "Arrange cards on the 1024 x 600 grid while the emulator updates beside you.",
  "Flash once over USB, then send future changes over the air from the same flow.",
];

const benefits = [
  "A wall panel that feels built for the household, not the demo home it came from.",
  "Fewer Home Assistant dashboards to maintain because the panel layout generates firmware-ready config.",
  "Guests get obvious controls: lights, locks, media, climate, and TV without opening an app.",
  "Night use stays calm with wake-on-approach, screensavers, and quick security context.",
];

const screens = [
  { src: "/aurora/screens/dashboard.png", alt: "Aurora dashboard screen", label: "Dashboard" },
  { src: "/aurora/screens/controls.png", alt: "Aurora room controls screen", label: "Controls" },
  { src: "/aurora/screens/media.png", alt: "Aurora media library screen", label: "Media" },
  { src: "/aurora/screens/tv-remote.png", alt: "Aurora TV remote screen", label: "TV" },
];

export default function Home() {
  return (
    <main>
      <section className="hero-section">
        <nav className="topbar" aria-label="Primary navigation">
          <a className="brand" href="#top" aria-label="Aurora home">
            <span className="brand-mark" />
            Aurora
          </a>
          <div className="nav-links">
            <a href="#library">Library</a>
            <a href="#emulator">Emulator</a>
            <a href="#setup">Setup</a>
            <a href="#benefits">Benefits</a>
            <a href="/demo-builder">Demo Builder</a>
          </div>
        </nav>

        <div className="hero-grid" id="top">
          <div className="hero-copy">
            <p className="eyebrow">Home Assistant touch dashboard for Guition ESP32-P4</p>
            <h1>Aurora turns a smart home panel into something anyone can configure.</h1>
            <p className="hero-text">
              Build a polished wall dashboard, bind it to your real Home Assistant entities,
              preview the 1024 x 600 panel, and flash firmware without hand-editing YAML.
            </p>
            <div className="hero-actions" aria-label="Page shortcuts">
              <a className="primary-action" href="/demo-builder">Try demo builder</a>
              <a className="secondary-action" href="#library">Explore components</a>
            </div>
            <div className="proof-strip" aria-label="Aurora highlights">
              <span>No-code configurator</span>
              <span>Live panel emulator</span>
              <span>ESPHome + LVGL</span>
            </div>
          </div>

          <div className="hero-device" aria-label="Aurora panel preview">
            <img src="/aurora/hero.png" alt="Aurora dashboard running on the 7 inch panel" />
          </div>
        </div>
      </section>

      <section className="config-section" aria-labelledby="config-title">
        <div className="section-heading">
          <p className="eyebrow">Web configuration</p>
          <h2 id="config-title">Design the panel from the browser.</h2>
          <p>
            The configurator connects to Home Assistant, discovers the entities you already
            use, and turns layout choices into firmware-ready Aurora screens.
          </p>
        </div>
        <div className="config-grid">
          <div className="config-image">
            <img src="/aurora/configurator.png" alt="Aurora web configurator with component palette, live emulator, and inspector" />
          </div>
          <div className="config-panel">
            <h3>What the builder gives you</h3>
            <a className="inline-demo-link" href="/demo-builder">Open the credential-free demo builder</a>
            <ul>
              <li>Drag cards onto a 6 x 5 layout grid.</li>
              <li>Bind every card to your own Home Assistant entity.</li>
              <li>Preview exact panel proportions before flashing.</li>
              <li>Generate rooms, pages, sensors, and YAML from saved layout data.</li>
            </ul>
          </div>
        </div>
      </section>

      <section className="library-section" id="library" aria-labelledby="library-title">
        <div className="section-heading compact">
          <p className="eyebrow">Component library</p>
          <h2 id="library-title">Everything the web config can build.</h2>
        </div>
        <div className="component-grid">
          {components.map((component) => (
            <article className="component-card" key={component.name}>
              <span className="component-dot" />
              <h3>{component.name}</h3>
              <p>{component.detail}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="emulator-section" id="emulator" aria-labelledby="emulator-title">
        <div className="section-heading">
          <p className="eyebrow">Dashboard and emulator</p>
          <h2 id="emulator-title">Preview the actual experience before it reaches the wall.</h2>
          <p>
            Aurora pairs a live browser emulator with real firmware screenshots, so layout
            decisions stay grounded in what the panel will show at arm's length.
          </p>
        </div>
        <div className="screen-row">
          {screens.map((screen) => (
            <figure className="screen-card" key={screen.label}>
              <img src={screen.src} alt={screen.alt} />
              <figcaption>{screen.label}</figcaption>
            </figure>
          ))}
        </div>
      </section>

      <section className="setup-section" id="setup" aria-labelledby="setup-title">
        <div className="setup-copy">
          <p className="eyebrow">Ease of setup</p>
          <h2 id="setup-title">From clone to custom panel in four clear moves.</h2>
          <p>
            Aurora keeps the advanced ESPHome work under the hood. The user-facing path is
            about connecting, choosing, previewing, and flashing.
          </p>
        </div>
        <ol className="setup-list">
          {setupSteps.map((step, index) => (
            <li key={step}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <p>{step}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="benefits-section" id="benefits" aria-labelledby="benefits-title">
        <div className="section-heading compact">
          <p className="eyebrow">End-user benefits</p>
          <h2 id="benefits-title">A better daily smart home surface.</h2>
        </div>
        <div className="benefit-grid">
          {benefits.map((benefit) => (
            <article className="benefit-card" key={benefit}>
              <p>{benefit}</p>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
