"use client";

import { useMemo, useState } from "react";

type CardType =
  | "light"
  | "climate"
  | "media"
  | "security"
  | "camera"
  | "weather"
  | "tv_remote"
  | "notifications"
  | "room_controls";

type Entity = {
  id: string;
  name: string;
  domain: string;
  room: string;
};

type DemoCard = {
  id: number;
  type: CardType;
  title: string;
  entity: string;
  room: string;
  size: "single" | "wide" | "tall";
};

const entityCatalog: Entity[] = [
  { id: "light.living_room_main", name: "Living Room Main", domain: "light", room: "Living Room" },
  { id: "light.kitchen_island", name: "Kitchen Island", domain: "light", room: "Kitchen" },
  { id: "light.bedroom_lamps", name: "Bedroom Lamps", domain: "light", room: "Bedroom" },
  { id: "fan.bedroom_ceiling", name: "Bedroom Ceiling Fan", domain: "fan", room: "Bedroom" },
  { id: "switch.patio_string_lights", name: "Patio String Lights", domain: "switch", room: "Patio" },
  { id: "cover.family_room_shades", name: "Family Room Shades", domain: "cover", room: "Family Room" },
  { id: "lock.front_door", name: "Front Door", domain: "lock", room: "Entry" },
  { id: "binary_sensor.garage_entry", name: "Garage Entry", domain: "binary_sensor", room: "Garage" },
  { id: "camera.driveway", name: "Driveway Camera", domain: "camera", room: "Exterior" },
  { id: "camera.front_porch", name: "Front Porch Camera", domain: "camera", room: "Entry" },
  { id: "media_player.spotify_living_room", name: "Spotify Living Room", domain: "media_player", room: "Living Room" },
  { id: "media_player.lg_oled", name: "LG OLED", domain: "media_player", room: "Living Room" },
  { id: "climate.downstairs", name: "Downstairs Climate", domain: "climate", room: "Downstairs" },
  { id: "weather.home", name: "Home Forecast", domain: "weather", room: "Home" },
  { id: "sensor.outdoor_temperature", name: "Outdoor Temperature", domain: "sensor", room: "Exterior" },
  { id: "person.alex", name: "Alex", domain: "person", room: "Home" },
  { id: "script.goodnight", name: "Goodnight", domain: "script", room: "Home" },
  { id: "scene.movie_time", name: "Movie Time", domain: "scene", room: "Living Room" },
];

const cardPalette: Array<{
  type: CardType;
  label: string;
  description: string;
  domains: string[];
  defaultSize: DemoCard["size"];
}> = [
  { type: "light", label: "Light", description: "Toggle and dim a light or switch.", domains: ["light", "switch"], defaultSize: "single" },
  { type: "climate", label: "Climate", description: "Show comfort and thermostat context.", domains: ["climate", "weather", "sensor"], defaultSize: "wide" },
  { type: "media", label: "Media", description: "Now playing, source, and transport controls.", domains: ["media_player"], defaultSize: "wide" },
  { type: "security", label: "Security", description: "Locks, sensors, and household presence.", domains: ["lock", "binary_sensor", "person"], defaultSize: "wide" },
  { type: "camera", label: "Camera", description: "A live camera card for panel pages.", domains: ["camera"], defaultSize: "wide" },
  { type: "weather", label: "Weather", description: "Forecast and outdoor condition card.", domains: ["weather", "sensor"], defaultSize: "single" },
  { type: "tv_remote", label: "TV Remote", description: "D-pad, volume, apps, and trackpad entry.", domains: ["media_player"], defaultSize: "wide" },
  { type: "notifications", label: "Notifications", description: "Recent alerts with optional actions.", domains: ["script", "scene", "binary_sensor"], defaultSize: "wide" },
  { type: "room_controls", label: "Room Controls", description: "Generated room page starter.", domains: ["light", "fan", "switch", "cover"], defaultSize: "tall" },
];

const starterCards: DemoCard[] = [
  { id: 1, type: "light", title: "Living Room", entity: "light.living_room_main", room: "Living Room", size: "single" },
  { id: 2, type: "media", title: "Now Playing", entity: "media_player.spotify_living_room", room: "Living Room", size: "wide" },
  { id: 3, type: "climate", title: "Downstairs", entity: "climate.downstairs", room: "Downstairs", size: "wide" },
  { id: 4, type: "security", title: "Entry", entity: "lock.front_door", room: "Entry", size: "wide" },
];

const labelForType = (type: CardType) => cardPalette.find((item) => item.type === type)?.label ?? type;
const domainsForType = (type: CardType) => cardPalette.find((item) => item.type === type)?.domains ?? [];

function findEntity(entityId: string) {
  return entityCatalog.find((entity) => entity.id === entityId) ?? entityCatalog[0];
}

function entityOptions(type: CardType) {
  const domains = domainsForType(type);
  return entityCatalog.filter((entity) => domains.includes(entity.domain));
}

export default function DemoBuilder() {
  const [cards, setCards] = useState<DemoCard[]>(starterCards);
  const [selectedId, setSelectedId] = useState(starterCards[0].id);

  const selectedCard = cards.find((card) => card.id === selectedId) ?? cards[0];
  const selectedEntity = selectedCard ? findEntity(selectedCard.entity) : entityCatalog[0];

  const generatedLayout = useMemo(
    () => ({
      project: "aurora-demo-layout",
      device: "guition-esp32-p4-jc1060p470",
      page: "Home",
      grid: { columns: 6, rows: 5, width: 1024, height: 600 },
      cards: cards.map((card, index) => ({
        slot: index + 1,
        type: card.type,
        title: card.title,
        entity: card.entity,
        room: card.room,
        size: card.size,
      })),
    }),
    [cards],
  );

  function addCard(type: CardType) {
    const paletteItem = cardPalette.find((item) => item.type === type) ?? cardPalette[0];
    const firstEntity = entityOptions(type)[0] ?? entityCatalog[0];
    const nextId = Math.max(0, ...cards.map((card) => card.id)) + 1;
    const nextCard: DemoCard = {
      id: nextId,
      type,
      title: paletteItem.label,
      entity: firstEntity.id,
      room: firstEntity.room,
      size: paletteItem.defaultSize,
    };
    setCards((current) => [...current, nextCard].slice(0, 10));
    setSelectedId(nextId);
  }

  function updateSelected(update: Partial<DemoCard>) {
    setCards((current) => current.map((card) => (card.id === selectedId ? { ...card, ...update } : card)));
  }

  function removeSelected() {
    setCards((current) => {
      const remaining = current.filter((card) => card.id !== selectedId);
      setSelectedId(remaining[0]?.id ?? 0);
      return remaining;
    });
  }

  function resetDemo() {
    setCards(starterCards);
    setSelectedId(starterCards[0].id);
  }

  return (
    <main className="builder-page">
      <nav className="topbar builder-topbar" aria-label="Demo builder navigation">
        <a className="brand" href="/" aria-label="Back to Aurora showcase">
          <span className="brand-mark" />
          Aurora
        </a>
        <div className="nav-links">
          <a href="/">Showcase</a>
          <a href="#palette">Palette</a>
          <a href="#layout-output">Layout JSON</a>
        </div>
      </nav>

      <section className="builder-hero">
        <div>
          <p className="eyebrow">Demo builder</p>
          <h1>Try the Aurora configurator with generic Home Assistant entities.</h1>
          <p className="hero-text">
            Add cards, bind them to sample entities, inspect the panel preview, and see the layout data Aurora would turn into firmware screens.
          </p>
        </div>
        <div className="entity-summary" aria-label="Demo entity catalog summary">
          <strong>{entityCatalog.length}</strong>
          <span>sample entities across lights, locks, media, climate, cameras, weather, scenes, and room controls.</span>
        </div>
      </section>

      <section className="builder-workspace" aria-label="Aurora demo builder workspace">
        <aside className="builder-panel palette-panel" id="palette">
          <div className="panel-heading">
            <p className="eyebrow">Component palette</p>
            <h2>Add cards</h2>
          </div>
          <div className="palette-list">
            {cardPalette.map((item) => (
              <button className="palette-button" key={item.type} type="button" onClick={() => addCard(item.type)}>
                <span>{item.label}</span>
                <small>{item.description}</small>
              </button>
            ))}
          </div>
        </aside>

        <section className="emulator-panel" aria-label="1024 by 600 panel emulator">
          <div className="emulator-toolbar">
            <div>
              <p className="eyebrow">Panel emulator</p>
              <h2>Home page preview</h2>
            </div>
            <button className="text-button" type="button" onClick={resetDemo}>Reset demo</button>
          </div>
          <div className="demo-device-frame">
            <div className="demo-device-screen">
              <div className="demo-nav-rail" aria-hidden="true">
                <span />
                <span />
                <span />
                <span />
                <span />
              </div>
              <div className="demo-dashboard-grid">
                {cards.map((card) => {
                  const entity = findEntity(card.entity);
                  return (
                    <button
                      className={`demo-card ${card.size} ${selectedId === card.id ? "selected" : ""}`}
                      key={card.id}
                      type="button"
                      onClick={() => setSelectedId(card.id)}
                    >
                      <span className="demo-card-type">{labelForType(card.type)}</span>
                      <strong>{card.title}</strong>
                      <small>{entity.name}</small>
                      <span className="demo-card-room">{card.room}</span>
                    </button>
                  );
                })}
                {cards.length === 0 ? <p className="empty-preview">Add a card from the palette to start a layout.</p> : null}
              </div>
            </div>
          </div>
        </section>

        <aside className="builder-panel inspector-panel">
          <div className="panel-heading">
            <p className="eyebrow">Inspector</p>
            <h2>Bind the selected card</h2>
          </div>
          {selectedCard ? (
            <div className="inspector-form">
              <label>
                Card title
                <input value={selectedCard.title} onChange={(event) => updateSelected({ title: event.target.value })} />
              </label>
              <label>
                Component type
                <select
                  value={selectedCard.type}
                  onChange={(event) => {
                    const nextType = event.target.value as CardType;
                    const nextEntity = entityOptions(nextType)[0] ?? selectedEntity;
                    updateSelected({ type: nextType, entity: nextEntity.id, room: nextEntity.room });
                  }}
                >
                  {cardPalette.map((item) => (
                    <option value={item.type} key={item.type}>{item.label}</option>
                  ))}
                </select>
              </label>
              <label>
                Entity binding
                <select
                  value={selectedCard.entity}
                  onChange={(event) => {
                    const entity = findEntity(event.target.value);
                    updateSelected({ entity: entity.id, room: entity.room, title: selectedCard.title || entity.name });
                  }}
                >
                  {entityOptions(selectedCard.type).map((entity) => (
                    <option value={entity.id} key={entity.id}>{entity.name} - {entity.id}</option>
                  ))}
                </select>
              </label>
              <label>
                Room
                <input value={selectedCard.room} onChange={(event) => updateSelected({ room: event.target.value })} />
              </label>
              <label>
                Card size
                <select value={selectedCard.size} onChange={(event) => updateSelected({ size: event.target.value as DemoCard["size"] })}>
                  <option value="single">Single</option>
                  <option value="wide">Wide</option>
                  <option value="tall">Tall</option>
                </select>
              </label>
              <div className="entity-facts">
                <span>Domain: {selectedEntity.domain}</span>
                <span>Room: {selectedEntity.room}</span>
              </div>
              <button className="danger-button" type="button" onClick={removeSelected}>Remove card</button>
            </div>
          ) : (
            <p className="empty-inspector">Select or add a card to edit its entity binding.</p>
          )}
        </aside>
      </section>

      <section className="layout-output-section" id="layout-output" aria-labelledby="layout-output-title">
        <div className="section-heading compact">
          <p className="eyebrow">Generated output</p>
          <h2 id="layout-output-title">Demo layout data</h2>
          <p>
            This is a browser-only preview of the structure Aurora can generate from a layout: page, card type, title, entity, room, and size.
          </p>
        </div>
        <pre className="layout-output"><code>{JSON.stringify(generatedLayout, null, 2)}</code></pre>
      </section>
    </main>
  );
}