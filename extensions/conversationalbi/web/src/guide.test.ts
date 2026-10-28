import { describe, expect, it } from "vitest";
import type { ModelDescription, ModelField } from "./api";
import { splitQuestion, splits, starterQuestions } from "./guide";

const field = (name: string, extra: Partial<ModelField> = {}): ModelField =>
  ({ name, description: "", type: "string", dimension: true, time: false, synonyms: [], ...extra });

// A slice of the flights model as describe_model returns it.
const flights: ModelDescription = {
  model: { database: "db-" + "a".repeat(32), namespace: ["ai_flights"], name: "flights" },
  name: "Flights", description: "Flights.", instructions: "", examples: [], unreadableTables: [], problems: [],
  datasets: [
    { name: "AIRCRAFT", description: "", table: "ai_flights.aircraft", primaryKey: ["tail_nr"],
      fields: [field("tail_nr"), field("name"), field("model"), field("year", { type: "int" })] },
    { name: "AIRPORT", description: "", table: "ai_flights.airports", primaryKey: ["code"], fields: [field("code"), field("name")] },
    { name: "FLIGHT", description: "", table: "ai_flights.flights", primaryKey: ["id"],
      fields: [field("id"), field("carrier_code"), field("tail_nr"), field("route_id"), field("dep_delay", { type: "double" }),
        field("scheduled_departure", { type: "timestamptz", time: true }), field("date", { type: "date", time: true })] },
    { name: "CARRIER", description: "", table: "ai_flights.carriers", primaryKey: ["code"], fields: [field("code"), field("name")] },
    { name: "RUNWAY", description: "", table: "ai_flights.runways", primaryKey: ["airport_code"], fields: [field("length")] },
  ],
  relationships: [
    { name: "flight_carrier", from: "FLIGHT", to: "CARRIER", fromColumns: ["carrier_code"], toColumns: ["code"] },
    { name: "flight_aircraft", from: "FLIGHT", to: "AIRCRAFT", fromColumns: ["tail_nr"], toColumns: ["tail_nr"] },
  ],
  metrics: [
    { name: "average_departure_delay", description: "Unit: minutes.", dataset: "FLIGHT", unit: "minutes", synonyms: [] },
    { name: "cancellation_pct", description: "Unit: percent.", dataset: "FLIGHT", unit: "percent", synonyms: [] },
  ],
  dimensions: {
    FLIGHT: [
      { field: "AIRCRAFT.tail_nr", path: ["flight_aircraft"] }, { field: "AIRCRAFT.name", path: ["flight_aircraft"] },
      { field: "AIRCRAFT.model", path: ["flight_aircraft"] }, { field: "AIRCRAFT.year", path: ["flight_aircraft"] },
      { field: "AIRPORT.name", via: [["flight_route", "route_departure_airport"], ["flight_route", "route_destination_airport"]], ambiguous: true },
      { field: "FLIGHT.id", path: [] }, { field: "FLIGHT.carrier_code", path: [] }, { field: "FLIGHT.dep_delay", path: [] },
      { field: "FLIGHT.scheduled_departure", path: [] }, { field: "FLIGHT.date", path: [] },
      { field: "CARRIER.code", path: ["flight_carrier"] }, { field: "CARRIER.name", path: ["flight_carrier"] },
    ],
  },
};

describe("the guide's questions name the model's own fields", () => {
  it("prefers the owner's examples", () => {
    expect(starterQuestions({ ...flights, examples: ["a?", "b?", "c?"] }, 2)).toEqual(["a?", "b?"]);
  });

  it("builds questions from the metrics and what they split by", () => {
    expect(starterQuestions(flights)).toEqual([
      "Show average_departure_delay per month of FLIGHT.date.",
      "Which CARRIER.name has the highest cancellation_pct?",
      "What is the overall average_departure_delay?",
      "Show cancellation_pct by AIRCRAFT.name.",
    ]);
  });

  it("offers nothing without a metric it can run", () => {
    expect(starterQuestions({ ...flights, metrics: [{ ...flights.metrics[0], dataset: null }] })).toEqual([]);
  });

  it("names the join path when a dimension is reachable in several ways", () => {
    const airport = splits(flights, "cancellation_pct").find((d) => d.field === "AIRPORT.name")!;
    expect(airport.ambiguous).toBe(true);
    expect(splitQuestion("cancellation_pct", airport, airport.via![0]))
      .toBe("Show cancellation_pct by AIRPORT.name via flight_route → route_departure_airport.");
  });
});
