import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountMenu, teams } from "./components/AccountMenu";

afterEach(() => {
  cleanup();
  document.documentElement.dataset.theme = "dark";
});

describe("the account menu", () => {
  it("offers only the theme switch before sign-in", () => {
    const { getAllByRole } = render(<AccountMenu />);
    expect(getAllByRole("button").map((b) => b.textContent)).toEqual(["Light / ○"]);
  });

  it("holds the theme switch and Sign out under the person's name", async () => {
    const signOut = vi.fn(async () => undefined);
    const { getByRole, getAllByRole, container } = render(<AccountMenu name="Ada Lovelace" signOut={signOut} />);
    const toggle = getByRole("button", { name: "Ada Lovelace" });
    expect(container.querySelector(".account-menu")?.hasAttribute("hidden")).toBe(true);
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(getAllByRole("button").map((b) => b.textContent)).toEqual(["Ada Lovelace", "Light / ○", "Sign out"]);
    fireEvent.click(getByRole("button", { name: "Switch to light mode" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(signOut).toHaveBeenCalledOnce());
  });

  it("shows who is signed in, with their teams, and Getting started in a new tab", () => {
    const details = teams([
      { team: "t1", teamName: "Sales", role: "reader" }, { team: "t2", teamName: "Finance", role: "admin" },
      { team: "t3", teamName: "Ops", role: "writer" }, { team: "t4", teamName: "HR", role: "writer" },
    ]);
    expect(details).toEqual(["Sales · Read", "Finance · Administrator", "Ops · Read & write", "+1 more teams"]);
    const { getByRole, container } = render(
      <AccountMenu name="Ada Lovelace" details={details} guide="https://docs.test/cbi/" signOut={() => undefined} />);
    fireEvent.click(getByRole("button", { name: "Ada Lovelace" }));
    expect(container.querySelector(".account-identity")?.textContent)
      .toBe("Ada LovelaceSales · ReadFinance · AdministratorOps · Read & write+1 more teams");
    const guide = getByRole("link", { name: "Getting started ↗" });
    expect(guide.getAttribute("href")).toBe("https://docs.test/cbi/");
    expect(guide.getAttribute("target")).toBe("_blank");
    expect([...container.querySelectorAll(".account-menu > .account-item")].map((n) => n.textContent))
      .toEqual(["Getting started ↗", "Light / ○", "Sign out"]);
  });

  it("closes on Escape and gives focus back to the toggle", () => {
    const { getByRole, container } = render(<AccountMenu name="Ada Lovelace" signOut={() => undefined} />);
    const toggle = getByRole("button", { name: "Ada Lovelace" });
    fireEvent.click(toggle);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(container.querySelector(".account-menu")?.hasAttribute("hidden")).toBe(true);
    expect(document.activeElement).toBe(toggle);
  });
});
