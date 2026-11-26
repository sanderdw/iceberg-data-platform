import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EnvironmentsNotice, SettingsPage, type EnvironmentEntry } from "./components/Settings";

const entry = (environment: string, enabled: boolean): EnvironmentEntry => ({
  team: "t1", environment, enabled, enabledBy: enabled ? "Ada" : null, databases: enabled ? [{ id: "db-1", name: "sales" }] : [],
});

let calls: { url: string; method: string }[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    calls.push({ url, method });
    const body = method === "GET"
      ? { environments: [entry("development", true), entry("acceptance", false), entry("production", false)] }
      : entry(url.split("/").pop()!, method === "POST");
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the Settings page", () => {
  it("has Environments as its first option", () => {
    const { getByRole } = render(<SettingsPage memberships={[]} />);
    const first = getByRole("navigation", { name: "Settings" }).querySelector("a");
    expect(first?.querySelector("strong")?.textContent).toBe("Environments");
    expect(first?.getAttribute("aria-current")).toBe("page");
    expect(first?.getAttribute("href")).toBe("#/settings/environments");
  });

  it("says what the setting does, what turning it off does and who can change it", () => {
    const { getByRole, getByText } = render(<SettingsPage memberships={[]} />);
    expect(getByRole("heading", { level: 2 }).textContent).toBe("Turn Conversational BI on per team environment");
    for (const term of ["When it is on", "When it is turned off", "Who can change it"]) expect(getByText(term)).toBeTruthy();
  });

  it("shows On and Off per environment from list_environments", async () => {
    const { container, findByText } = render(<SettingsPage memberships={[{ team: "t1", teamName: "Sales", role: "reader" }]} />);
    await findByText("Enabled by Ada");
    const states = [...container.querySelectorAll(".environments-state")].map((s) => s.textContent);
    expect(states).toEqual(["On", "Off", "Off"]);
    expect(container.querySelector(".environments-cell.on strong")?.textContent).toBe("Development");
    expect(calls[0]).toEqual({ url: "/api/v1/teams/t1/environments", method: "GET" });
  });

  it("gives a team administrator Enable and Disable", async () => {
    const { findByRole, getAllByRole } = render(<SettingsPage memberships={[{ team: "t1", teamName: "Sales", role: "admin" }]} />);
    await findByRole("button", { name: "Disable Conversational BI for Sales in Development" });
    expect(getAllByRole("button", { name: /^Enable Conversational BI for Sales/ })).toHaveLength(2);
  });

  it("shows other members who enables it, without buttons", async () => {
    const { findByText, queryAllByRole, getByText } = render(
      <SettingsPage memberships={[{ team: "t1", teamName: "Sales", role: "writer" }]} />);
    await findByText("Enabled by Ada");
    expect(queryAllByRole("button")).toHaveLength(0);
    expect(getByText("A team administrator of this team enables and disables it.")).toBeTruthy();
  });

  it("enables after the confirmation, and not before", async () => {
    const changed = vi.fn();
    const { findByRole, getByRole } = render(
      <SettingsPage memberships={[{ team: "t1", teamName: "Sales", role: "admin" }]} onChange={changed} />);
    fireEvent.click(await findByRole("button", { name: "Enable Conversational BI for Sales in Production" }));
    expect(document.querySelector(".environments-dialog h2")?.textContent).toBe("Enable Conversational BI for Sales in Production?");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.click(getByRole("button", { name: "Enable" }));
    await waitFor(() => expect(calls).toContainEqual({ url: "/api/v1/teams/t1/environments/production", method: "POST" }));
    await waitFor(() => expect(changed).toHaveBeenCalledWith(entry("production", true)));
    expect(document.querySelector(".environments-dialog")).toBeNull();
  });

  it("does nothing when the confirmation is cancelled", async () => {
    const { findByRole, getByRole } = render(<SettingsPage memberships={[{ team: "t1", teamName: "Sales", role: "admin" }]} />);
    fireEvent.click(await findByRole("button", { name: "Disable Conversational BI for Sales in Development" }));
    fireEvent.click(getByRole("button", { name: "Cancel" }));
    expect(document.querySelector(".environments-dialog")).toBeNull();
    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });
});

describe("the not-enabled notice", () => {
  it("links other members to Settings", () => {
    const { getByRole, container } = render(
      <EnvironmentsNotice membership={{ team: "t1", teamName: "Sales", role: "reader" }} environment="production" />);
    expect(container.textContent).toContain("Conversational BI is not enabled for Sales in Production.");
    expect(getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("#/settings/environments?team=t1");
  });
});
