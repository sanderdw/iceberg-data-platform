// The flat diagrams of chapters 2, 6, 7 and 8, in the Nothing style: black, grey hairlines, Space Mono
// labels, Space Grotesk text, and red only for the one thing the voice is about.
import React from "react";
import type { Beat } from "../lib/edl";
import { T, useT } from "../lib/i18n";
import { C, FONT } from "../lib/theme";
import { FigureCard, mono, useBeat } from "./frame";

const W = 1640;
const H = 760;

const clamp01 = (t: number) => Math.max(0, Math.min(1, t));
const ease = (t: number) => {
  const x = clamp01(t);
  return x * x * (3 - 2 * x);
};
const span = (p: number, a: number, b: number) => ease((p - a) / (b - a));

/** Progress through the whole shot, beat by beat: beat i covers [i, i+1). */
const step = (index: number, p: number) => index + p;

const body = (size: number, color: string = C.text, weight = 400): React.CSSProperties => ({
  fontFamily: FONT.body,
  fontSize: size,
  color,
  fontWeight: weight,
  lineHeight: 1.25,
});

const Stage: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ position: "relative", width: W, height: H }}>{children}</div>
);

const Box: React.FC<{
  x: number;
  y: number;
  w: number;
  h: number;
  on?: number; // 0..1 appearance
  hot?: boolean; // the one the voice is about
  dashed?: boolean;
  fill?: string;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}> = ({ x, y, w, h, on = 1, hot = false, dashed = false, fill = C.surface, children, style }) => (
  <div
    style={{
      position: "absolute",
      left: x,
      top: y,
      width: w,
      height: h,
      boxSizing: "border-box",
      border: `${hot ? 2 : 1}px ${dashed ? "dashed" : "solid"} ${hot ? C.white : C.borderStrong}`,
      borderRadius: 12,
      background: fill,
      opacity: on,
      transform: `translateY(${(1 - on) * 14}px)`,
      padding: "18px 22px",
      display: "flex",
      flexDirection: "column",
      justifyContent: "center",
      gap: 8,
      ...style,
    }}
  >
    {children}
  </div>
);

/** A line between two points, drawn from start to end as `draw` goes 0..1, with dots on both ends. */
const Link: React.FC<{ from: [number, number]; to: [number, number]; draw?: number; color?: string; dashed?: boolean; width?: number }> = ({
  from,
  to,
  draw = 1,
  color = C.textDim,
  dashed = false,
  width = 2,
}) => {
  if (draw <= 0) return null;
  const x = from[0] + (to[0] - from[0]) * draw;
  const y = from[1] + (to[1] - from[1]) * draw;
  return (
    <g>
      <line x1={from[0]} y1={from[1]} x2={x} y2={y} stroke={color} strokeWidth={width} strokeDasharray={dashed ? "6 8" : undefined} strokeLinecap="round" />
      <circle cx={from[0]} cy={from[1]} r={5} fill={C.black} stroke={color} strokeWidth={2} />
      {draw >= 1 ? <circle cx={to[0]} cy={to[1]} r={5} fill={C.black} stroke={color} strokeWidth={2} /> : null}
    </g>
  );
};

const Lines: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <svg width={W} height={H} style={{ position: "absolute", left: 0, top: 0, overflow: "visible" }}>
    {children}
  </svg>
);

// ---------- 02 · De bouwstenen ----------
const functions = (t: T): Array<[string, string, string, string]> => [
  [t("Opslag", "Storage"), t("Schijf van de server", "The server's disk"), "RustFS", "Amazon S3 · Azure Blob · GCS"],
  [t("Bestandsformaat", "File format"), "PGDATA blob storage", t("Parquet, kolomgericht", "Parquet, columnar"), "ORC · Avro"],
  [t("Tabelformaat", "Table format"), t("Intern", "Internal"), "Apache Iceberg", "Delta Lake · Apache Hudi"],
  [t("Metadata-catalogus", "Metadata catalog"), t("Systeemcatalogus", "System catalog"), "Apache Polaris", "Lakekeeper · AWS Glue"],
  [t("Catalogus-API", "Catalog API"), t("Postgres-protocol, ODBC", "Postgres protocol, ODBC"), "Iceberg REST API", t("Elke Iceberg REST-catalogus", "Any Iceberg REST catalog")],
  ["Query engine", t("Ingebouwd", "Built in"), "DuckDB", "PyIceberg · Polars · Spark · Snowflake"],
];

export const FunctionsDiagram: React.FC<{ beats: Beat[]; pieceFrom: number; frames: number }> = ({ beats, pieceFrom, frames }) => {
  const { index, p } = useBeat(beats, pieceFrom);
  const t = step(index, p);
  const tr = useT();
  const FUNCTIONS = functions(tr);
  const cols = [0, 380, 800, 1190];
  const widths = [340, 390, 360, 450];
  const top = 96;
  const rowH = 104;
  const current = index >= 1 && index <= 6 ? index - 1 : -1;
  const merged = span(t, 7, 7.25); // last beat: Postgres is one system
  return (
    <FigureCard
      fig="Fig 2"
      title={tr("De bouwstenen", "The building blocks")}
      hint={current >= 0 ? FUNCTIONS[current][0] : index >= 7 ? tr("Eén systeem of bouwstenen", "One system or building blocks") : tr("Functies", "Functions")}
      frames={frames}
    >
      <Stage>
        {[tr("Functie", "Function"), "Postgres", "Iceberg Data Platform", tr("Alternatieven", "Alternatives")].map((h, c) => (
          <div key={h} style={{ position: "absolute", left: c === 2 ? cols[c] - 16 : cols[c], top: 30, ...mono(22, c === 2 ? C.white : C.textMid) }}>
            {h}
          </div>
        ))}
        <div style={{ position: "absolute", left: 0, right: 0, top: 72, height: 1, background: C.borderStrong }} />
        {/* Postgres as one box around all its cells once the last beat says so. */}
        <div
          style={{
            position: "absolute",
            left: cols[1] - 16,
            top: top - 6,
            width: widths[1],
            height: rowH * FUNCTIONS.length + 4,
            border: `2px solid ${C.white}`,
            borderRadius: 14,
            opacity: merged,
          }}
        />
        {FUNCTIONS.map((row, r) => {
          const on = span(t, r + 1, r + 1.15);
          const hot = r === current;
          const y = top + r * rowH;
          return (
            <div key={row[0]} style={{ position: "absolute", left: 0, top: y, width: W, height: rowH, opacity: on }}>
              <div style={{ position: "absolute", left: cols[0], top: 24, display: "flex", alignItems: "center", gap: 14 }}>
                <div style={{ width: 10, height: 10, borderRadius: 5, background: hot ? C.red : "transparent", border: hot ? "none" : `1px solid ${C.borderStrong}` }} />
                <span style={body(30, hot ? C.white : C.text, 500)}>{row[0]}</span>
              </div>
              <div style={{ position: "absolute", left: cols[1], top: 26, ...body(28, hot ? C.text : C.textMid) }}>{row[1]}</div>
              {/* Iceberg: each function its own block, with air between them. */}
              <div
                style={{
                  position: "absolute",
                  left: cols[2] - 16,
                  top: 10,
                  width: widths[2],
                  height: rowH - 20,
                  boxSizing: "border-box",
                  border: `${hot ? 2 : 1}px solid ${hot ? C.white : C.borderStrong}`,
                  borderRadius: 10,
                  background: C.surface,
                  padding: "0 16px",
                  display: "flex",
                  alignItems: "center",
                  ...body(28, C.white, 500),
                }}
              >
                {row[2]}
              </div>
              <div style={{ position: "absolute", left: cols[3], top: 26, ...body(26, hot ? C.text : C.textDim) }}>{row[3]}</div>
              <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 1, background: C.border }} />
            </div>
          );
        })}
      </Stage>
    </FigureCard>
  );
};

// ---------- 02 · Zo is het hier ingericht ----------
export const PlatformDiagram: React.FC<{ beats: Beat[]; pieceFrom: number; frames: number }> = ({ beats, pieceFrom, frames }) => {
  const { p } = useBeat(beats, pieceFrom);
  const t = useT();
  const a = span(p, 0.05, 0.3);
  const b = span(p, 0.3, 0.6);
  return (
    <FigureCard fig="Fig 3" title={t("Eén database", "One database")} hint={t("Database = catalogus + bucket", "Database = catalog + bucket")} frames={frames}>
      <Stage>
        <Lines>
          <Link from={[620, 380]} to={[880, 210]} draw={b} color={C.text} />
          <Link from={[620, 380]} to={[880, 550]} draw={b} color={C.text} />
          <Link from={[1260, 300]} to={[1260, 460]} draw={span(p, 0.6, 0.8)} color={C.textDim} dashed />
        </Lines>
        <Box x={140} y={290} w={480} h={180} on={a} hot>
          <div style={mono(20, C.textMid)}>Database</div>
          <div style={body(48, C.white, 500)}>multiverse</div>
          <div style={body(24, C.textMid)}>team analytics · Development</div>
        </Box>
        <Box x={880} y={120} w={640} h={180} on={b}>
          <div style={mono(20, C.textMid)}>{t("Catalogus · Apache Polaris", "Catalog · Apache Polaris")}</div>
          <div style={body(34, C.white, 500)}>db-352c28f0…</div>
          <div style={body(24, C.textMid)}>{t("metadata bewaard in Postgres", "metadata stored in Postgres")}</div>
        </Box>
        <Box x={880} y={460} w={640} h={180} on={b}>
          <div style={mono(20, C.textMid)}>Bucket · RustFS</div>
          <div style={body(34, C.white, 500)}>db-db-352c28f0…</div>
          <div style={body(24, C.textMid)}>{t("Parquet- en metadata-bestanden", "Parquet and metadata files")}</div>
        </Box>
      </Stage>
    </FigureCard>
  );
};

// ---------- 06 · Echt weg ----------
export const PermanentDiagram: React.FC<{ beats: Beat[]; pieceFrom: number; frames: number }> = ({ beats, pieceFrom, frames }) => {
  const { index, p } = useBeat(beats, pieceFrom);
  const t = step(index, p);
  const tr = useT();
  const s1 = span(t, 1.05, 1.3); // rewrite
  const s2 = span(t, 1.38, 1.62); // expire
  const s3 = span(t, 1.7, 1.95); // remove files
  const active = t < 1.05 ? -1 : t < 1.38 ? 0 : t < 1.7 ? 1 : 2;
  const steps = [tr("Herschrijven", "Rewrite"), tr("Snapshots laten verlopen", "Expire snapshots"), tr("Bestanden opruimen", "Remove files")];
  const snapX = [180, 480, 780, 1080];
  const lineY = 250;
  const file = (x: number, label: string, what: string, on: number, gone = 0, hot = false, jerry = false) => (
    <>
      {gone > 0 ? <Box x={x} y={360} w={330} h={130} on={gone} dashed fill="transparent" /> : null}
      <Box x={x} y={360} w={330} h={130} on={on * (1 - gone)} hot={hot} fill={C.surface}>
        <div style={mono(18, C.textMid)}>{label}</div>
        <div style={body(26, jerry ? C.white : C.textMid, 500)}>{what}</div>
      </Box>
    </>
  );
  return (
    <FigureCard fig="Fig 5" title={tr("Echt weg", "Really gone")} hint={active < 0 ? tr("Drie stappen", "Three steps") : steps[active]} frames={frames}>
      <Stage>
        {/* The three steps along the top, spread evenly over the timeline's width. */}
        <div style={{ position: "absolute", left: 120, width: 1380, top: 20, display: "flex", justifyContent: "space-between" }}>
          {steps.map((s, i) => (
            <div key={s} style={{ position: "relative", display: "flex", alignItems: "center", gap: 16, opacity: span(t, 0.15 + i * 0.2, 0.3 + i * 0.2) }}>
              <div style={{ ...mono(30, active === i ? C.white : C.textDim), width: 52 }}>0{i + 1}</div>
              <div style={body(32, active === i ? C.white : C.textMid, 500)}>{s}</div>
              {active === i ? <div style={{ position: "absolute", right: -26, width: 10, height: 10, borderRadius: 5, background: C.white }} /> : null}
            </div>
          ))}
        </div>
        <Lines>
          <line x1={120} y1={lineY} x2={1500} y2={lineY} stroke={C.borderStrong} strokeWidth={2} />
          {/* Snapshots: 1 append, 2 delete, then the rewrite's two. Expired ones fade to hollow. */}
          {snapX.map((x, i) => {
            const exists = i < 2 ? 1 : s1;
            const expired = i < 3 ? s2 : 0;
            if (exists <= 0) return null;
            return (
              <g key={x} opacity={exists}>
                <circle cx={x} cy={lineY} r={14} fill={expired > 0.5 ? C.black : C.text} stroke={expired > 0.5 ? C.textDim : C.white} strokeWidth={2} />
                {expired > 0.5 ? <line x1={x - 10} y1={lineY - 10} x2={x + 10} y2={lineY + 10} stroke={C.textDim} strokeWidth={2} /> : null}
              </g>
            );
          })}
        </Lines>
        {snapX.map((x, i) =>
          (i < 2 || s1 > 0) ? (
            <div key={x} style={{ position: "absolute", left: x - 90, top: lineY + 30, width: 180, textAlign: "center", ...mono(18, s2 > 0.5 && i < 3 ? C.textDim : C.textMid), opacity: i < 2 ? 1 : s1 }}>
              {["snapshot 1", "snapshot 2", "snapshot 3", "snapshot 4"][i]}
            </div>
          ) : null,
        )}
        {/* The files under them. */}
        {file(80, tr("Parquet · origineel", "Parquet · original"), tr("met Jerry", "with Jerry"), 1, s3, active === 2, true)}
        {file(460, tr("delete-bestand", "delete file"), tr("alleen posities", "positions only"), 1, 0)}
        {file(980, tr("Parquet · herschreven", "Parquet · rewritten"), tr("zonder Jerry", "without Jerry"), s1, 0, active === 0)}
        <div style={{ position: "absolute", left: 80, top: 520, ...body(26, C.red, 500), opacity: s3 }}>{tr("verwijderd uit de bucket", "removed from the bucket")}</div>
        <div style={{ position: "absolute", left: 980, top: 520, ...body(26, C.textMid), opacity: s1 }}>{tr("nieuw bestand, zonder Jerry", "new file, without Jerry")}</div>
        <div style={{ position: "absolute", left: 120, top: 620, ...body(28, C.textMid), opacity: span(t, 0.1, 0.4) * (1 - s1) }}>
          {tr(
            "Na een delete staat Jerry nog in het oude Parquet-bestand: de nieuwe snapshot markeert de rijen alleen.",
            "After a delete, Jerry is still in the old Parquet file: the new snapshot only marks the rows.",
          )}
        </div>
      </Stage>
    </FigureCard>
  );
};

// ---------- 07 · Snowflake en Iceberg: dezelfde scheiding ----------
const Layer: React.FC<{ x: number; y: number; w: number; label: string; tool: string; on: number; hot?: boolean; dashed?: boolean }> = ({ x, y, w, label, tool, on, hot, dashed }) => (
  <Box x={x} y={y} w={w} h={120} on={on} hot={hot} dashed={dashed}>
    <div style={mono(18, C.textMid)}>{label}</div>
    <div style={body(30, C.white, 500)}>{tool}</div>
  </Box>
);

export const CompareDiagram: React.FC<{ beats: Beat[]; pieceFrom: number; frames: number }> = ({ beats, pieceFrom, frames }) => {
  const { index, p } = useBeat(beats, pieceFrom);
  const t = step(index, p);
  const left = span(t, 0.05, 0.3);
  const right = span(t, 0.35, 0.65);
  const engine = span(t, 1.1, 1.4);
  const tr = useT();
  return (
    <FigureCard
      fig="Fig 6"
      title={tr("Snowflake en Iceberg", "Snowflake and Iceberg")}
      hint={index === 0 ? tr("Zelfde scheiding", "Same separation") : tr("Kies je engine", "Choose your engine")}
      frames={frames}
    >
      <Stage>
        <div style={{ position: "absolute", left: 60, top: 0, ...mono(24, C.white) }}>Snowflake</div>
        <div style={{ position: "absolute", left: 900, top: 0, ...mono(24, C.white), opacity: right }}>Apache Iceberg</div>
        {/* Snowflake: one managed boundary around all three layers. */}
        <div style={{ position: "absolute", left: 40, top: 50, width: 680, height: 600, border: `2px solid ${C.borderStrong}`, borderRadius: 18, opacity: left }} />
        <div style={{ position: "absolute", left: 70, top: 610, ...mono(18, C.textMid), opacity: left }}>{tr("volledig beheerd door Snowflake", "fully managed by Snowflake")}</div>
        <Layer x={80} y={90} w={600} label="Compute" tool={tr("Snowflake-engine", "Snowflake engine")} on={left} />
        <Layer x={80} y={260} w={600} label="Metadata" tool={tr("Snowflake-catalogus", "Snowflake catalog")} on={left} />
        <Layer x={80} y={430} w={600} label={tr("Opslag", "Storage")} tool={tr("Snowflake-opslag", "Snowflake storage")} on={left} />
        {/* Iceberg: the same three layers, each your own choice; compute on both sides. */}
        <Layer x={900} y={90} w={300} label={tr("Producent", "Producer")} tool={tr("jouw engine", "your engine")} on={right} hot={engine > 0.5} dashed />
        <Layer x={1240} y={90} w={300} label={tr("Consument", "Consumer")} tool={tr("jouw engine", "your engine")} on={right} hot={engine > 0.5} dashed />
        <Layer x={900} y={260} w={640} label="Metadata" tool={tr("Iceberg REST-catalogus", "Iceberg REST catalog")} on={right} />
        <Layer x={900} y={430} w={640} label={tr("Opslag", "Storage")} tool={tr("Je eigen bucket", "Your own bucket")} on={right} />
        <div style={{ position: "absolute", left: 900, top: 610, ...mono(18, C.textMid), opacity: right }}>{tr("open standaard, losse bouwstenen", "open standard, separate building blocks")}</div>
      </Stage>
    </FigureCard>
  );
};

// ---------- 07 · Producent en consument ----------
const Chip: React.FC<{ x: number; y: number; label: string; on: number; hot?: boolean; gone?: number }> = ({ x, y, label, on, hot, gone = 0 }) => (
  <Box x={x} y={y} w={300} h={84} on={on * (1 - gone * 0.75)} hot={hot} style={{ padding: "0 22px" }}>
    <div style={body(30, C.white, 500)}>{label}</div>
  </Box>
);

export const EnginesDiagram: React.FC<{ beats: Beat[]; pieceFrom: number; frames: number }> = ({ beats, pieceFrom, frames }) => {
  const { index, p } = useBeat(beats, pieceFrom);
  const t = step(index, p);
  const producers = ["DuckDB", "PyIceberg", "Spark", "Snowflake"];
  const consumers = ["DuckDB", "Polars", "Snowflake", "Trino"];
  const prodOn = span(t, 0.05, 0.3);
  const write = span(t, 0.3, 0.7);
  const gone = span(t, 1.1, 1.4); // the producer engine is gone; the data stays
  const consOn = span(t, 2.05, 2.3);
  const read = span(t, 2.3, 2.7);
  const tr = useT();
  const hint = index === 0 ? tr("Producent", "Producer") : index === 1 ? tr("De data blijft", "The data stays") : tr("Consument", "Consumer");
  return (
    <FigureCard fig="Fig 7" title={tr("Kies je engine", "Choose your engine")} hint={hint} frames={frames}>
      <Stage>
        <Lines>
          {producers.map((_, i) => (
            <Link key={i} from={[340, 140 + i * 130]} to={[630, 380]} draw={write * (1 - gone)} color={C.textDim} />
          ))}
          {consumers.map((_, i) => (
            <Link key={i} from={[1010, 380]} to={[1300, 140 + i * 130]} draw={read} color={C.textDim} />
          ))}
        </Lines>
        {producers.map((label, i) => (
          <Chip key={label} x={40} y={98 + i * 130} label={label} on={prodOn} gone={gone} hot={index === 0 && i === 0} />
        ))}
        <Box x={630} y={250} w={380} h={260} hot={index === 1} on={span(t, 0, 0.15)}>
          <div style={mono(18, C.textMid)}>{tr("Iceberg-tabel", "Iceberg table")}</div>
          <div style={body(32, C.white, 500)}>{tr("bucket + catalogus", "bucket + catalog")}</div>
          <div style={body(22, C.textMid)}>Parquet · metadata · REST API</div>
        </Box>
        {consumers.map((label, i) => (
          <Chip key={label} x={1300} y={98 + i * 130} label={label} on={consOn} hot={index === 2 && i === 0} />
        ))}
      </Stage>
    </FigureCard>
  );
};

// ---------- 08 · Uitwisselen ----------
export const CombineDiagram: React.FC<{ beats: Beat[]; pieceFrom: number; frames: number }> = ({ beats, pieceFrom, frames }) => {
  const { index, p, frame } = useBeat(beats, pieceFrom);
  const t = step(index, p);
  const left = span(t, 0.05, 0.3);
  const right = span(t, 1.05, 1.35);
  const moving = t >= 1.6 && t < 3.2 ? 1 : span(t, 1.4, 1.6) * (1 - span(t, 3.2, 3.4));
  const producers = ["A", "B", "C"];
  const consumer: [number, number] = [1450, 380];
  const sources = producers.map((_, i) => [1010, 150 + i * 230] as [number, number]);
  const tr = useT();
  const hint = index === 0 ? tr("Eén platform", "One platform") : index <= 2 ? tr("Data-overdracht", "Data transfer") : tr("Uitwisselen, niet vervangen", "Exchange, not replace");
  return (
    <FigureCard fig="Fig 8" title={tr("Combineren of uitwisselen", "Combine or exchange")} hint={hint} frames={frames}>
      <Stage>
        <div style={{ position: "absolute", left: 40, top: 0, ...mono(24, C.white), opacity: left }}>{tr("Geïntegreerd platform", "Integrated platform")}</div>
        <div style={{ position: "absolute", left: 860, top: 0, ...mono(24, C.white), opacity: right }}>Iceberg</div>
        {/* Left: all producers' data inside one compute platform, joined in place. */}
        <div style={{ position: "absolute", left: 40, top: 50, width: 700, height: 640, border: `2px solid ${index === 0 ? C.white : C.borderStrong}`, borderRadius: 18, opacity: left }} />
        {producers.map((n, i) => (
          <Box key={n} x={80} y={110 + i * 150} w={260} h={110} on={left}>
            <div style={mono(18, C.textMid)}>
              {tr("Producent", "Producer")} {n}
            </div>
            <div style={body(28, C.white, 500)}>{tr("dataproduct", "data product")}</div>
          </Box>
        ))}
        <Lines>
          {producers.map((_, i) => (
            <Link key={i} from={[340, 165 + i * 150]} to={[470, 315]} draw={left} color={C.textDim} />
          ))}
        </Lines>
        <Box x={470} y={240} w={230} h={150} on={left} hot={index === 0}>
          <div style={mono(18, C.textMid)}>{tr("Eén compute", "One compute")}</div>
          <div style={body(28, C.white, 500)}>{tr("combineren", "combine")}</div>
        </Box>
        <div style={{ position: "absolute", left: 80, top: 600, ...body(24, C.textMid), opacity: left }}>{tr("De data verlaat het platform niet.", "The data never leaves the platform.")}</div>
        {/* Right: every producer its own bucket and catalog; the consumer fetches and computes itself. */}
        {producers.map((n, i) => (
          <Box key={n} x={860} y={100 + i * 230} w={300} h={130} on={right}>
            <div style={mono(18, C.textMid)}>
              {tr("Producent", "Producer")} {n}
            </div>
            <div style={body(26, C.white, 500)}>{tr("bucket + catalogus", "bucket + catalog")}</div>
          </Box>
        ))}
        <Lines>
          {sources.map((s, i) => (
            <Link key={i} from={[1160, s[1] + 15]} to={[consumer[0] - 120, consumer[1]]} draw={right} color={C.borderStrong} dashed />
          ))}
          {/* Packets of data travelling to the consumer: the transfer that always happens. */}
          {moving > 0
            ? sources.map((s, i) =>
                [0, 1, 2].map((k) => {
                  const u = ((frame / 45 + k / 3 + i * 0.17) % 1 + 1) % 1;
                  const x = 1160 + (consumer[0] - 120 - 1160) * u;
                  const y = s[1] + 15 + (consumer[1] - s[1] - 15) * u;
                  return <rect key={`${i}-${k}`} x={x - 7} y={y - 7} width={14} height={14} rx={3} fill={i === 0 && k === 0 ? C.red : C.text} opacity={moving * (0.4 + 0.6 * Math.sin(u * Math.PI))} />;
                }),
              )
            : null}
        </Lines>
        <Box x={consumer[0] - 120} y={consumer[1] - 90} w={250} h={180} on={right} hot={index === 1 || index === 2}>
          <div style={mono(18, C.textMid)}>{tr("Consument", "Consumer")}</div>
          <div style={body(28, C.white, 500)}>{tr("eigen engine", "own engine")}</div>
          <div style={body(22, C.textMid)}>{tr("haalt op, rekent zelf", "fetches, computes")}</div>
        </Box>
        <div style={{ position: "absolute", left: 860, top: 690, ...body(24, C.textMid), opacity: right }}>{tr("Er vindt altijd een data-overdracht plaats.", "There is always a data transfer.")}</div>
      </Stage>
    </FigureCard>
  );
};

// ---------- 05 · Drie manieren om te deleten ----------
const ROWS = ["Rick Sanchez", "Morty Smith", "Summer Smith", "Beth Smith", "Jerry Smith", "Abadango Cluster Princess"];

const DataFile: React.FC<{ x: number; y: number; title: string; rows: string[]; mark?: number; strike?: number; old?: number; on: number; hot?: boolean }> = ({
  x,
  y,
  title,
  rows,
  mark = -1,
  strike = 0,
  old = 0,
  on,
  hot = false,
}) => {
  const t = useT();
  return (
    <Box x={x} y={y} w={470} h={70 + rows.length * 34} on={on} hot={hot || old > 0.5} style={{ justifyContent: "flex-start", gap: 4 }}>
      <div style={{ ...mono(17, C.textMid), marginBottom: 6 }}>{title}</div>
      {rows.map((r, i) => (
        <div key={r} style={body(22, i === mark ? C.white : C.textMid, i === mark ? 500 : 400)}>
          <span style={{ position: "relative", display: "inline-block" }}>
            {r}
            {i === mark && strike > 0 ? (
              <div style={{ position: "absolute", left: 0, top: "52%", height: 2, width: `${strike * 100}%`, background: C.white }} />
            ) : null}
          </span>
        </div>
      ))}
      {old > 0 ? <div style={{ position: "absolute", left: 0, top: "calc(100% + 14px)", ...mono(16, C.red), opacity: old }}>{t("oude data is er nog", "old data is still there")}</div> : null}
    </Box>
  );
};

export const DeletesDiagram: React.FC<{ beats: Beat[]; pieceFrom: number; frames: number }> = ({ beats, pieceFrom, frames }) => {
  const { index, p } = useBeat(beats, pieceFrom);
  const t = step(index, p);
  const cols = [20, 590, 1160];
  const on = [span(t, 0.02, 0.15), span(t, 1.02, 1.15), span(t, 2.02, 2.15)];
  const marker = [span(t, 0.3, 0.5), span(t, 1.3, 1.5), span(t, 2.3, 2.5)];
  const old = span(t, 3.05, 3.25);
  const tr = useT();
  const heads = [
    [tr("Formaatversie 2", "Format version 2"), "merge-on-read"],
    [tr("Formaatversie 3", "Format version 3"), "merge-on-read"],
    ["Copy-on-write", tr("v2 en v3", "v2 and v3")],
  ];
  const hint =
    index === 0 ? tr("Delete-bestand", "Delete file") : index === 1 ? "Deletion vector" : index === 2 ? "Copy-on-write" : tr("De oude versie blijft", "The old version stays");
  return (
    <FigureCard fig="Fig 4" title={tr("Een delete", "A delete")} hint={hint} frames={frames}>
      <Stage>
        {heads.map(([h, s], i) => (
          <div key={h} style={{ position: "absolute", left: cols[i], top: 0, opacity: on[i] }}>
            <div style={mono(24, index === i ? C.white : C.text)}>{h}</div>
            <div style={{ ...body(22, C.textMid), marginTop: 6 }}>{s}</div>
          </div>
        ))}
        {/* v2: the data file stays, a Parquet delete file lists file and position. */}
        <DataFile x={cols[0]} y={90} title={tr("Parquet-bestand", "Parquet file")} rows={ROWS} mark={4} strike={marker[0]} on={on[0]} hot={index === 0} old={old} />
        <Box x={cols[0]} y={450} w={470} h={150} on={marker[0]} hot={index === 0}>
          <div style={mono(17, C.textMid)}>{tr("delete-bestand · Parquet", "delete file · Parquet")}</div>
          <div style={{ fontFamily: FONT.mono, fontSize: 21, color: C.white }}>01a15e86….parquet · {tr("rij 5", "row 5")}</div>
          <div style={body(20, C.textMid)}>{tr("één bestand per delete, ze stapelen op", "one file per delete, they pile up")}</div>
        </Box>
        {/* v3: a deletion vector, a bitmap in a Puffin file, one per data file. */}
        <DataFile x={cols[1]} y={90} title={tr("Parquet-bestand", "Parquet file")} rows={ROWS} mark={4} strike={marker[1]} on={on[1]} hot={index === 1} old={old} />
        <Box x={cols[1]} y={450} w={470} h={150} on={marker[1]} hot={index === 1}>
          <div style={mono(17, C.textMid)}>deletion vector · Puffin</div>
          <div style={{ display: "flex", gap: 6, margin: "6px 0" }}>
            {ROWS.map((r, i) => (
              <div key={r} style={{ width: 26, height: 26, borderRadius: 4, border: `1.5px solid ${C.textMid}`, background: i === 4 ? C.white : "transparent" }} />
            ))}
          </div>
          <div style={body(20, C.textMid)}>{tr("één bitmap per databestand", "one bitmap per data file")}</div>
        </Box>
        {/* Copy-on-write: a new data file without the row; the old one is no longer in the current snapshot. */}
        <DataFile x={cols[2]} y={90} title={tr("Parquet-bestand · oud", "Parquet file · old")} rows={ROWS} mark={4} on={on[2] * (1 - 0.5 * marker[2])} old={old} />
        <DataFile x={cols[2]} y={450} title={tr("Parquet-bestand · nieuw", "Parquet file · new")} rows={ROWS.filter((_, i) => i !== 4)} on={marker[2]} hot={index === 2} />
      </Stage>
    </FigureCard>
  );
};
