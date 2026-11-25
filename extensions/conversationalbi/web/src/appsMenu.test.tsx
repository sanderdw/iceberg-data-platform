import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { type Apps, AppsMenu } from "./components/AppsMenu";

const apps: Apps = {
  workspace: [{ id: "catalog", title: "Catalog", url: "http://localhost:3002/#catalog" }],
  extensions: [
    { id: "conversationalbi", title: "Conversational BI", purpose: "Ask questions", origin: "http://localhost:3007" },
    { id: "other", title: "other", purpose: null, origin: "http://localhost:3009" },
  ],
};

afterEach(cleanup);

describe("the Apps menu", () => {
  it("gives each menu its own id", () => {
    const { getAllByRole } = render(<><AppsMenu apps={apps} current="conversationalbi" />
      <AppsMenu apps={apps} current="conversationalbi" /></>);
    const ids = getAllByRole("button", { name: "Apps" }).map((b) => b.getAttribute("aria-controls"));
    expect(new Set(ids).size).toBe(2);
    for (const id of ids) expect(document.getElementById(id!)?.className).toBe("apps-menu");
  });

  it("closes on Escape and on a click outside", () => {
    const { getByRole } = render(<AppsMenu apps={apps} current="conversationalbi" />);
    const toggle = getByRole("button", { name: "Apps" });
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    fireEvent.click(document.body);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("shows an extension without a purpose by its title only", () => {
    const { getByText } = render(<AppsMenu apps={apps} current="conversationalbi" />);
    expect(getByText("other").parentElement?.textContent).toBe("other");
  });
});
