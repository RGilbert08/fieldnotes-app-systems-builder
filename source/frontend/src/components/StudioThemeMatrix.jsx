import "../studio-theme-fonts.css";
import "./StudioThemeMatrix.css";
import { STUDIO_THEMES } from "../utils/studioThemes";

export default function StudioThemeMatrix({
  selectedThemeId,
  onSelect,
  disabled = false,
  title = "Choose a visual theme",
  description = "Theme choices set canvas, card, accent, text, border, and font defaults. You can still edit individual components.",
}) {
  return (
    <section
      aria-label="15 audience theme presets"
      className="studio-theme-matrix"
    >
      <div className="studio-theme-matrix__heading">
        <div>
          <p className="studio-theme-matrix__eyebrow">15 multi-audience presets</p>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <span className="studio-theme-matrix__count" aria-label="15 themes">
          15 themes
        </span>
      </div>
      <div className="studio-theme-matrix__grid">
        {STUDIO_THEMES.map((theme) => {
          const isSelected = selectedThemeId === theme.id;
          return (
            <button
              aria-pressed={isSelected}
              className={`studio-theme-card${isSelected ? " is-selected" : ""}`}
              disabled={disabled}
              key={theme.id}
              onClick={() => onSelect(theme.id)}
              style={{
                "--theme-canvas": theme.tokens.canvas,
                "--theme-surface": theme.tokens.surface,
                "--theme-accent": theme.tokens.accent,
                "--theme-text": theme.tokens.text,
                "--theme-border": theme.tokens.border,
                "--theme-font": theme.fontStack,
              }}
              type="button"
            >
              <span className="studio-theme-card__preview" aria-hidden="true">
                <span className="studio-theme-card__sample-header" />
                <span className="studio-theme-card__sample-card">
                  <span className="studio-theme-card__sample-line" />
                  <span className="studio-theme-card__sample-line is-short" />
                  <span className="studio-theme-card__sample-action" />
                </span>
              </span>
              <span className="studio-theme-card__copy">
                <strong>{theme.name}</strong>
                <span>{theme.description}</span>
                <small>{theme.fontFamily}</small>
              </span>
              <span className="studio-theme-card__swatches" aria-hidden="true">
                {[theme.tokens.canvas, theme.tokens.surface, theme.tokens.accent, theme.tokens.text]
                  .map((color) => (
                    <span key={color} style={{ backgroundColor: color }} />
                  ))}
              </span>
              <span className="studio-theme-card__selection">
                {isSelected ? "Selected" : "Select theme"}
              </span>
            </button>
          );
        })}
      </div>
      <p aria-live="polite" className="studio-theme-matrix__status">
        {selectedThemeId
          ? `${STUDIO_THEMES.find((theme) => theme.id === selectedThemeId)?.name || "Theme"} selected.`
          : "Choose a theme to continue."}
      </p>
    </section>
  );
}