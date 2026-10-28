import React from "react";

/** The language of the video: the English cut sets it in its EDL (edit/build.py --voice gemini-en). */
export type Lang = "nl" | "en";
export const LangContext = React.createContext<Lang>("nl");

export type T = (nl: string, en: string) => string;

/** The text in the video's language: t("Dutch", "English"). */
export const useT = (): T => {
  const lang = React.useContext(LangContext);
  return (nl, en) => (lang === "en" ? en : nl);
};
