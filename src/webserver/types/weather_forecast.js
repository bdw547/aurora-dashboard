// Legacy read-only forecast card: displays tomorrow's high / low temperature.
var WEATHER_FORECAST_CARD_METADATA = {
  entity: WEATHER_CARD_METADATA.entity,
  preview: WEATHER_CARD_METADATA.preview,
};

registerButtonType("weather_forecast", {
  label: "Weather Forecast",
  allowInSubpage: true,
  hideLabel: true,
  cardMetadata: WEATHER_FORECAST_CARD_METADATA,
  isAvailable: function () {
    return false;
  },
  onSelect: function (b) {
    b.label = "";
    b.icon = "Auto";
    b.icon_on = "Auto";
    b.sensor = "";
    b.unit = "";
    b.precision = "tomorrow";
  },
  renderSettings: function (panel, b, slot, helpers) {
    helpers.renderCardEntityField(panel, b, helpers, WEATHER_FORECAST_CARD_METADATA);
  },
  renderPreview: function (b, helpers) {
    return {
      iconHtml: cardSensorPreviewHtml(b, helpers, "18/10", temperatureUnitSymbol(), "sp-forecast-preview", "sp-forecast-value"),
      labelHtml: cardBadgeLabelHtml(helpers, "Temperatures Tomorrow", WEATHER_FORECAST_CARD_METADATA.preview.forecastBadge),
    };
  },
});
