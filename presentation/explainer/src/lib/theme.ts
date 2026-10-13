// Palette as named tokens: a deep night-blue world with a few saturated accents.
// Every shape is two-tone: a base colour and a darker "shade" for its shadow side.
export const C = {
  night: "#0A1433",
  night2: "#132656",
  dawn: "#2B2F6E",
  sky: "#1B3170",
  seaTop: "#0F3C74",
  sea: "#0B2C5C",
  seaDeep: "#06173A",
  foam: "#7FD6FF",

  ice: "#E8F8FF",
  iceShade: "#A8DDF6",
  iceDeep: "#5DB3E4",
  iceDeepShade: "#3C8CC4",

  teal: "#2FE3C3",
  tealShade: "#17AF95",
  coral: "#FF6584",
  coralShade: "#D9406A",
  sun: "#FFCB4D",
  sunShade: "#EFA21F",
  violet: "#8D70FF",
  violetShade: "#6649E3",
  green: "#5BE38A",
  greenShade: "#2FB862",
  amber: "#FFB23F",
  red: "#FF5468",

  skin: "#FFD3B0",
  skinShade: "#EDAE86",
  paper: "#F4F7FF",
  paperShade: "#C9D3F0",

  white: "#F4F7FF",
  muted: "#93A6DB",
  dim: "#4C5F9A",
  ink: "#0A1433",
} as const;

export const FONT = "Nunito";
export const W = 1920;
export const H = 1080;

// Type scale for 1080p; nothing below 30 px so text stays readable on a phone.
export const T = {
  title: 120,
  big: 168,
  h1: 76,
  h2: 56,
  label: 44,
  small: 36,
  caption: 30,
} as const;
