// Fig 1: the Iceberg table format as the classic tree (catalog → metadata file → manifest list →
// manifest → data files), with the files of the characters table in the multiverse database as the
// example. Snapshot 1 is the load of chapter 3, snapshot 2 the delete of chapter 5; the bucket and
// catalog ids are shortened to readable names.
import React from "react";
import type { Beat } from "../lib/edl";
import { T, useT } from "../lib/i18n";
import { C, FONT } from "../lib/theme";
import { FigureCard, mono, useBeat } from "./frame";

const W = 1720;
const H = 770;

const clamp01 = (t: number) => Math.max(0, Math.min(1, t));
const ease = (t: number) => {
  const x = clamp01(t);
  return x * x * (3 - 2 * x);
};
const span = (p: number, a: number, b: number) => ease((p - a) / (b - a));

const body = (size: number, color: string = C.text, weight = 400): React.CSSProperties => ({
  fontFamily: FONT.body,
  fontSize: size,
  color,
  fontWeight: weight,
  lineHeight: 1.2,
});

const BUCKET = "s3://multiverse-bucket/rickandmorty/characters/";
const FILES = {
  meta1: "00001-9e1e5822….metadata.json",
  meta2: "00002-a5a6c329….metadata.json",
  list1: "snap-5300743934….avro",
  list2: "snap-5679415373….avro",
  man1: "528f293b-…-m0.avro",
  man2: "10b0d293-…-m0.avro",
  data1: "01a15e86-….parquet",
  data2: "1767e4c3-…-deletes.parquet",
};
const TABLE_PATH = "/v1/multiverse/namespaces/rickandmorty/tables/characters";

// Layout: the tree in two columns (snapshot 1 left, snapshot 2 right) inside the bucket, the catalog
// above it, and the client in its own column on the right, outside the layers.
const COL = [190, 730];
const NODE_W = 440;
const GUTTER_X = (COL[0] + NODE_W + COL[1]) / 2; // the free lane between the two columns
const ROW = { meta: 168, list: 312, man: 456, data: 612 };
const NODE_H = { meta: 108, list: 108, man: 108, data: 108 };
const CAT = { x: 300, y: 0, w: 640, h: 124 };
const BOX = { x: 170, y: 140, w: 1020, h: 622 }; // the bucket
const CLIENT = { x: 1290, y: 300, w: 420, h: 130 };

type Key = "cat" | "meta1" | "meta2" | "list1" | "list2" | "man1" | "man2" | "data1" | "data2" | "client" | "bucket" | "postgres";

const Node: React.FC<{
  x: number;
  y: number;
  w?: number;
  h: number;
  kind: string;
  file: string;
  role: string;
  on: number;
  hot: boolean;
  dim?: boolean;
  corner?: boolean;
}> = ({ x, y, w = NODE_W, h, kind, file, role, on, hot, dim = false, corner = true }) => (
  <div style={{ position: "absolute", left: x, top: y, width: w, height: h, opacity: on * (dim ? 0.4 : 1), transform: `translateY(${(1 - on) * 12}px)` }}>
    <svg width={w} height={h} style={{ position: "absolute", left: 0, top: 0, overflow: "visible" }}>
      <path
        d={corner ? `M18 1 H${w - 1} V${h - 1} H1 V18 Z` : `M1 1 H${w - 1} V${h - 1} H1 Z`}
        fill={hot ? "#161616" : C.surface}
        stroke={hot ? C.white : C.borderStrong}
        strokeWidth={hot ? 2.5 : 1.5}
      />
      {corner ? <path d="M1 18 H18 V1" fill="none" stroke={hot ? C.white : C.borderStrong} strokeWidth={hot ? 2.5 : 1.5} /> : null}
    </svg>
    <div style={{ position: "absolute", left: 28, top: 12, right: 16 }}>
      <div style={mono(16, hot ? C.white : C.textMid)}>{kind}</div>
      <div style={{ fontFamily: FONT.mono, fontSize: 20, color: hot ? C.white : C.text, marginTop: 6, whiteSpace: "nowrap" }}>{file}</div>
      <div style={{ ...body(19, hot ? C.text : C.textMid), marginTop: 6, whiteSpace: "nowrap" }}>{role}</div>
    </div>
  </div>
);

/** The catalog's pointer: one smooth curve from (x1,y1) down to (x2,y2), drawn as `draw` goes 0..1. */
const Pointer: React.FC<{ from: [number, number]; to: [number, number]; draw: number }> = ({ from, to, draw }) => {
  if (draw <= 0) return null;
  const [x1, y1] = from;
  const [x2, y2] = to;
  const my = y1 + (y2 - y1) * 0.55;
  return (
    <g>
      <path d={`M${x1} ${y1} C${x1} ${my} ${x2} ${my} ${x2} ${y2}`} fill="none" stroke={C.red} strokeWidth={3} pathLength={1} strokeDasharray={`${draw} 1`} strokeLinecap="round" />
      {draw >= 0.98 ? <path d={`M${x2 - 8} ${y2 - 12} L${x2} ${y2} L${x2 + 8} ${y2 - 12}`} fill="none" stroke={C.red} strokeWidth={3} strokeLinejoin="round" /> : null}
    </g>
  );
};

/** An arrow along straight segments through `points`, with rounded corners, drawn as `draw` goes 0..1. */
const Route: React.FC<{ points: Array<[number, number]>; draw: number }> = ({ points, draw }) => {
  if (draw <= 0) return null;
  const r = 10;
  let d = `M${points[0][0]} ${points[0][1]}`;
  for (let i = 1; i < points.length - 1; i++) {
    const [px, py] = points[i - 1];
    const [x, y] = points[i];
    const [nx, ny] = points[i + 1];
    const into = Math.min(r, Math.hypot(x - px, y - py) / 2);
    const out = Math.min(r, Math.hypot(nx - x, ny - y) / 2);
    const a: [number, number] = [x - Math.sign(x - px) * into, y - Math.sign(y - py) * into];
    const b: [number, number] = [x + Math.sign(nx - x) * out, y + Math.sign(ny - y) * out];
    d += ` L${a[0]} ${a[1]} Q${x} ${y} ${b[0]} ${b[1]}`;
  }
  const [ex, ey] = points[points.length - 1];
  const [sx, sy] = points[points.length - 2];
  d += ` L${ex} ${ey}`;
  const angle = Math.atan2(ey - sy, ex - sx);
  const wing = (turn: number) => `${ex - 12 * Math.cos(angle + turn)} ${ey - 12 * Math.sin(angle + turn)}`;
  return (
    <g>
      <path d={d} fill="none" stroke={C.textMid} strokeWidth={2} pathLength={1} strokeDasharray={`${draw} 1`} strokeLinecap="round" />
      {draw >= 0.98 ? <path d={`M${wing(0.6)} L${ex} ${ey} L${wing(-0.6)}`} fill="none" stroke={C.textMid} strokeWidth={2} strokeLinejoin="round" /> : null}
    </g>
  );
};

/** A straight connection between the client and something, with its protocol as a label. */
const Wire: React.FC<{ d: string; draw: number; label: string; at: [number, number]; head?: [number, number, number] }> = ({ d, draw, label, at, head }) => {
  if (draw <= 0) return null;
  return (
    <g>
      <path d={d} fill="none" stroke={C.white} strokeWidth={2} pathLength={1} strokeDasharray={`${draw} 1`} strokeLinejoin="round" />
      {head && draw >= 0.98 ? (
        <path
          d={`M${head[0] - 12 * Math.cos(head[2] - 0.5)} ${head[1] - 12 * Math.sin(head[2] - 0.5)} L${head[0]} ${head[1]} L${head[0] - 12 * Math.cos(head[2] + 0.5)} ${head[1] - 12 * Math.sin(head[2] + 0.5)}`}
          fill="none"
          stroke={C.white}
          strokeWidth={2}
        />
      ) : null}
      <text x={at[0]} y={at[1]} fill={C.textMid} opacity={draw} style={{ fontFamily: FONT.mono, fontSize: 17, letterSpacing: "0.08em" }}>
        {label}
      </text>
    </g>
  );
};

interface View {
  on: Partial<Record<Key, number>>;
  hot: Key | null;
  dim: Key[];
  arrows: Record<string, number>;
  rest: number; // client ↔ catalog over the REST API
  s3: number; // client ↔ bucket over S3
  s3Write: boolean;
  pointer: 1 | 2;
  pointerDraw: number;
  label: string; // what the line at the bottom left is about
  example: string; // the real path or request, from the recorded example
}

const pathOf = (file: string, folder: "metadata" | "data") => `${BUCKET}${folder}/${file}`;

function viewAt(beat: number, p: number, t: T): View {
  const base: View = {
    on: { cat: 1, bucket: 1, meta1: 1, list1: 1, man1: 1, data1: 1 },
    hot: null,
    dim: [],
    arrows: { m1l1: 1, l1m1: 1, m1d1: 1 },
    rest: 0,
    s3: 0,
    s3Write: false,
    pointer: 1,
    pointerDraw: 1,
    label: t("Tabel characters", "Table characters"),
    example: BUCKET,
  };
  if (beat === 0) {
    // The tree assembles from the bottom up: data, manifests, metadata, catalog.
    return {
      ...base,
      on: {
        bucket: span(p, 0.02, 0.15),
        data1: span(p, 0.1, 0.25),
        man1: span(p, 0.22, 0.37),
        list1: span(p, 0.34, 0.49),
        meta1: span(p, 0.46, 0.61),
        cat: span(p, 0.6, 0.75),
        client: span(p, 0.75, 0.9),
      },
      arrows: { m1d1: span(p, 0.3, 0.42), l1m1: span(p, 0.42, 0.54), m1l1: span(p, 0.54, 0.66) },
      pointerDraw: span(p, 0.72, 0.86),
    };
  }
  if (beat === 1) {
    const meta = p > 0.55;
    return {
      ...base,
      on: { ...base.on, client: 1 },
      hot: meta ? "meta1" : "data1",
      label: meta ? t("Metadata-bestand", "Metadata file") : t("Parquet-bestand", "Parquet file"),
      example: meta ? pathOf(FILES.meta1, "metadata") : pathOf(FILES.data1, "data"),
    };
  }
  if (beat === 2) {
    const steps: Array<[Key, string, string]> = [
      ["meta1", t("Metadata-bestand", "Metadata file"), pathOf(FILES.meta1, "metadata")],
      ["list1", "Manifest list", pathOf(FILES.list1, "metadata")],
      ["man1", "Manifest", pathOf(FILES.man1, "metadata")],
      ["data1", t("Parquet-bestand", "Parquet file"), pathOf(FILES.data1, "data")],
    ];
    const i = Math.min(3, Math.floor(p * 4));
    const [hot, label, example] = steps[i];
    return { ...base, on: { ...base.on, client: 1 }, hot, label, example };
  }
  if (beat === 3) {
    return {
      ...base,
      on: { ...base.on, client: 1 },
      hot: "cat",
      pointerDraw: span(p, 0.05, 0.4),
      label: "Polaris · characters →",
      example: pathOf(FILES.meta1, "metadata"),
    };
  }
  if (beat === 4) {
    // Reading: ask the catalog over REST for the table's metadata, then read the files straight from the bucket.
    const v: View = { ...base, on: { ...base.on, client: 1 } };
    v.rest = span(p, 0.05, 0.2);
    v.s3 = span(p, 0.42, 0.55);
    const path: Key[] = ["meta1", "list1", "man1", "data1"];
    const i = p < 0.25 ? -1 : Math.min(3, Math.floor((p - 0.25) / 0.17));
    v.hot = i < 0 ? "client" : path[i];
    v.label = i < 0 ? "REST GET" : i === 0 ? t("Antwoord van Polaris", "Response from Polaris") : "S3 GET";
    v.example = i < 0 ? TABLE_PATH : i === 0 ? pathOf(FILES.meta1, "metadata") : pathOf([FILES.list1, FILES.man1, FILES.data1][i - 1], i === 3 ? "data" : "metadata");
    return v;
  }
  if (beat >= 6) {
    // Postgres for comparison: everything in one server.
    return {
      ...base,
      on: { ...base.on, client: 1, data2: 1, man2: 1, list2: 1, meta2: 1, postgres: span(p, 0.05, 0.25) },
      arrows: { ...base.arrows, m2d2: 1, l2m2: 1, l2m1: 1, m2l1: 1, m2l2: 1 },
      dim: ["meta1"],
      pointer: 2,
      label: "Iceberg",
      example: t("catalogus · bucket · engine naar keuze", "catalog · bucket · engine of your choice"),
    };
  }
  // Writing: the client puts the new files in the bucket, commits over REST, and the catalog writes the
  // new metadata file and moves its pointer in one step.
  const v: View = {
    ...base,
    on: {
      ...base.on,
      client: 1,
      data2: span(p, 0.06, 0.16),
      man2: span(p, 0.18, 0.28),
      list2: span(p, 0.3, 0.4),
      meta2: span(p, 0.6, 0.7),
    },
    arrows: {
      ...base.arrows,
      m2d2: span(p, 0.22, 0.28),
      l2m2: span(p, 0.34, 0.4),
      l2m1: span(p, 0.36, 0.42),
      m2l1: span(p, 0.64, 0.7),
      m2l2: span(p, 0.66, 0.72),
    },
  };
  v.s3 = span(p, 0.02, 0.08) * (1 - span(p, 0.42, 0.46));
  v.s3Write = true;
  v.rest = span(p, 0.46, 0.54);
  const swap = span(p, 0.74, 0.84);
  v.pointer = swap < 0.5 ? 1 : 2;
  v.pointerDraw = swap < 0.5 ? 1 - swap * 2 : (swap - 0.5) * 2;
  v.dim = swap >= 1 ? ["meta1"] : [];
  const stage = p < 0.18 ? 0 : p < 0.3 ? 1 : p < 0.46 ? 2 : p < 0.6 ? 3 : p < 0.74 ? 4 : 5;
  v.hot = (["data2", "man2", "list2", "client", "meta2", "cat"] as Key[])[stage];
  v.label = ["S3 PUT", "S3 PUT", "S3 PUT", "REST POST (commit)", t("Polaris schrijft", "Polaris writes"), "Polaris · characters →"][stage];
  v.example = [
    pathOf(FILES.data2, "data"),
    pathOf(FILES.man2, "metadata"),
    pathOf(FILES.list2, "metadata"),
    TABLE_PATH,
    pathOf(FILES.meta2, "metadata"),
    pathOf(FILES.meta2, "metadata"),
  ][stage];
  return v;
}

const mid = (col: number) => COL[col] + NODE_W / 2;

export const TreeDiagram: React.FC<{ beats: Beat[]; pieceFrom: number; frames: number }> = ({ beats, pieceFrom, frames }) => {
  const { index, p } = useBeat(beats, pieceFrom);
  const t = useT();
  const v = viewAt(index, p, t);
  const on = (k: Key) => v.on[k] ?? 0;
  const hot = (k: Key) => v.hot === k;
  const dim = (k: Key) => v.dim.includes(k);
  const top = (row: keyof typeof ROW) => ROW[row];
  const bottom = (row: keyof typeof ROW) => ROW[row] + NODE_H[row];
  // Each column's arrows run straight down its middle.
  const down = (col: number, from: keyof typeof ROW, to: keyof typeof ROW): Array<[number, number]> => [
    [mid(col), bottom(from)],
    [mid(col), top(to)],
  ];
  // Snapshot 2 reuses snapshot 1's manifest list and manifest: out of its left side, down the lane, into their right side.
  const across = (from: keyof typeof ROW, to: keyof typeof ROW, lane: number): Array<[number, number]> => [
    [COL[1], ROW[from] + NODE_H[from] * 0.62],
    [GUTTER_X + lane, ROW[from] + NODE_H[from] * 0.62],
    [GUTTER_X + lane, ROW[to] + NODE_H[to] * 0.38],
    [COL[0] + NODE_W, ROW[to] + NODE_H[to] * 0.38],
  ];
  const pointerTo = mid(v.pointer - 1);
  const clientMidY = CLIENT.y + CLIENT.h / 2;
  return (
    <FigureCard
      fig="Fig 1"
      title={t("Een Iceberg-tabel", "An Iceberg table")}
      hint={
        <>
          {v.label}
          <span style={{ fontFamily: FONT.mono, fontSize: 22, letterSpacing: 0, textTransform: "none", color: C.textMid }}>{v.example}</span>
        </>
      }
      frames={frames}
    >
      <div style={{ position: "relative", width: W, height: H }}>
        {/* Layer labels on the left. */}
        {[
          [t("Catalogus", "Catalog"), 40],
          [t("Metadatalaag", "Metadata\nlayer"), ROW.meta + 4],
          [t("Datalaag", "Data\nlayer"), ROW.data + 4],
        ].map(([label, y]) => (
          <div key={label as string} style={{ position: "absolute", left: 0, top: y as number, ...mono(18, C.textMid), whiteSpace: "pre", lineHeight: 1.4 }}>
            {label}
          </div>
        ))}
        <div style={{ position: "absolute", left: CLIENT.x, top: CLIENT.y - 44, ...mono(18, C.textMid), opacity: on("client") }}>Engine</div>
        <svg width={W} height={H} style={{ position: "absolute", left: 0, top: 0, overflow: "visible" }}>
          {/* The bucket around both layers, with the boundary between them. */}
          <g opacity={on("bucket")}>
            <rect x={BOX.x} y={BOX.y} width={BOX.w} height={BOX.h} rx={18} fill="none" stroke={C.borderStrong} strokeWidth={1.5} />
            <line x1={BOX.x} y1={ROW.data - 24} x2={BOX.x + BOX.w} y2={ROW.data - 24} stroke={C.borderStrong} strokeWidth={1.5} strokeDasharray="8 10" />
          </g>
          {/* The client's column, set apart from the layers. */}
          <line x1={1230} y1={140} x2={1230} y2={H - 10} stroke={C.border} strokeWidth={1.5} opacity={on("client")} />
          {/* The catalog as a cylinder. */}
          <g opacity={on("cat")}>
            <path
              d={`M${CAT.x} ${CAT.y + 22} V${CAT.y + CAT.h - 22} A${CAT.w / 2} 22 0 0 0 ${CAT.x + CAT.w} ${CAT.y + CAT.h - 22} V${CAT.y + 22}`}
              fill={hot("cat") ? "#161616" : C.surface}
              stroke={hot("cat") ? C.white : C.borderStrong}
              strokeWidth={hot("cat") ? 2.5 : 1.5}
            />
            <ellipse cx={CAT.x + CAT.w / 2} cy={CAT.y + 22} rx={CAT.w / 2} ry={22} fill={C.surface} stroke={hot("cat") ? C.white : C.borderStrong} strokeWidth={hot("cat") ? 2.5 : 1.5} />
          </g>
          {/* Tree arrows. */}
          <Route points={down(0, "meta", "list")} draw={v.arrows.m1l1 ?? 0} />
          <Route points={down(0, "list", "man")} draw={v.arrows.l1m1 ?? 0} />
          <Route points={down(0, "man", "data")} draw={v.arrows.m1d1 ?? 0} />
          <Route points={down(1, "meta", "list")} draw={v.arrows.m2l2 ?? 0} />
          <Route points={down(1, "list", "man")} draw={v.arrows.l2m2 ?? 0} />
          <Route points={down(1, "man", "data")} draw={v.arrows.m2d2 ?? 0} />
          <Route points={across("meta", "list", -12)} draw={v.arrows.m2l1 ?? 0} />
          <Route points={across("list", "man", 12)} draw={v.arrows.l2m1 ?? 0} />
          {/* The catalog's pointer: the one red line. */}
          <Pointer from={[CAT.x + CAT.w / 2, CAT.y + 104]} to={[pointerTo, top("meta")]} draw={v.pointerDraw * on("cat")} />
          {/* The client talks to the catalog over REST, and to the bucket over S3. */}
          <Wire
            d={`M${CLIENT.x + 120} ${CLIENT.y} V${CAT.y + 68} H${CAT.x + CAT.w + 4}`}
            draw={v.rest}
            label={t("REST API · metadata en rechten", "REST API · metadata and permissions")}
            at={[CAT.x + CAT.w + 30, CAT.y + 56]}
            head={[CAT.x + CAT.w + 4, CAT.y + 68, Math.PI]}
          />
          <Wire
            d={v.s3Write ? `M${CLIENT.x} ${clientMidY + 40} H${BOX.x + BOX.w}` : `M${BOX.x + BOX.w} ${clientMidY + 40} H${CLIENT.x}`}
            draw={v.s3}
            label="S3"
            at={[BOX.x + BOX.w + 14, clientMidY + 26]}
            head={v.s3Write ? [BOX.x + BOX.w, clientMidY + 40, Math.PI] : [CLIENT.x, clientMidY + 40, 0]}
          />
        </svg>
        <div style={{ position: "absolute", left: BOX.x + BOX.w - 420, top: BOX.y + BOX.h - 34, width: 400, textAlign: "right", ...mono(17, C.textMid), opacity: on("bucket") }}>
          s3://multiverse-bucket/
        </div>
        <div style={{ position: "absolute", left: CAT.x, top: CAT.y + 40, width: CAT.w, textAlign: "center", opacity: on("cat") }}>
          <div style={mono(17, C.textMid)}>{t("Iceberg REST-catalogus · Apache Polaris", "Iceberg REST catalog · Apache Polaris")}</div>
          <div style={{ ...body(22, C.text), marginTop: 6 }}>{t("weet per tabel welk metadata-bestand actueel is", "knows the current metadata file of each table")}</div>
        </div>
        <Node
          x={COL[0]}
          y={ROW.meta}
          h={NODE_H.meta}
          kind={t("Metadata-bestand · s1", "Metadata file · s1")}
          file={FILES.meta1}
          role={t("schema en snapshots van de tabel", "the table's schema and snapshots")}
          on={on("meta1")}
          hot={hot("meta1")}
          dim={dim("meta1")}
        />
        <Node
          x={COL[1]}
          y={ROW.meta}
          h={NODE_H.meta}
          kind={t("Metadata-bestand · s1 + s2", "Metadata file · s1 + s2")}
          file={FILES.meta2}
          role={t("dezelfde tabel, één snapshot verder", "the same table, one snapshot later")}
          on={on("meta2")}
          hot={hot("meta2")}
        />
        <Node x={COL[0]} y={ROW.list} h={NODE_H.list} kind="Manifest list · s1" file={FILES.list1} role={t("welke manifests bij s1 horen", "which manifests belong to s1")} on={on("list1")} hot={hot("list1")} />
        <Node x={COL[1]} y={ROW.list} h={NODE_H.list} kind="Manifest list · s2" file={FILES.list2} role={t("welke manifests bij s2 horen", "which manifests belong to s2")} on={on("list2")} hot={hot("list2")} />
        <Node x={COL[0]} y={ROW.man} h={NODE_H.man} kind="Manifest" file={FILES.man1} role={t("databestanden, min en max per kolom", "data files, min and max per column")} on={on("man1")} hot={hot("man1")} />
        <Node x={COL[1]} y={ROW.man} h={NODE_H.man} kind="Manifest" file={FILES.man2} role={t("welk delete-bestand erbij komt", "the delete file that is added")} on={on("man2")} hot={hot("man2")} />
        <Node x={COL[0]} y={ROW.data} h={NODE_H.data} kind={t("Parquet-bestand", "Parquet file")} file={FILES.data1} role={t("de rijen zelf, kolomgericht", "the rows themselves, columnar")} on={on("data1")} hot={hot("data1")} />
        <Node x={COL[1]} y={ROW.data} h={NODE_H.data} kind={t("Parquet · delete-bestand", "Parquet · delete file")} file={FILES.data2} role={t("welke rijen niet meer meetellen", "which rows no longer count")} on={on("data2")} hot={hot("data2")} />
        <Node
          x={CLIENT.x}
          y={CLIENT.y}
          w={CLIENT.w}
          h={CLIENT.h}
          kind="Client"
          file="DuckDB"
          role={t("vraagt de catalogus, leest de bucket", "asks the catalog, reads the bucket")}
          on={on("client")}
          hot={hot("client")}
          corner={false}
        />
      </div>
      {/* Postgres for comparison: one server holds catalog, files and engine. */}
      <div
        style={{
          position: "absolute",
          right: 112,
          top: 640,
          width: 420,
          opacity: v.on.postgres ?? 0,
          border: `2px solid ${C.white}`,
          borderRadius: 14,
          background: C.surface,
          padding: "16px 22px",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <div style={mono(17, C.white)}>{t("Postgres · één server", "Postgres · one server")}</div>
        {[
          t("metadata en data hard gekoppeld", "metadata and data tightly coupled"),
          t("op de eigen schijf van de server", "on the server's own disk"),
          t("queries op diezelfde server", "queries on that same server"),
        ].map((line) => (
          <div key={line} style={{ ...body(20, C.text), borderTop: `1px solid ${C.borderStrong}`, paddingTop: 8 }}>
            {line}
          </div>
        ))}
      </div>
    </FigureCard>
  );
};
