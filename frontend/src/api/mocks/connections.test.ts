import { describe, expect, it } from "vitest";

import { ApiError } from "../http";
import { connectMockSourceCraft, disconnectMockSourceCraft, mockConnection } from "./connections";

describe("mock-подключение SourceCraft", () => {
  it("не принимает слишком короткий токен", () => {
    disconnectMockSourceCraft();
    expect(() => connectMockSourceCraft("123")).toThrow(ApiError);
    expect(mockConnection().connected).toBe(false);
  });

  it("подключается и отключается", () => {
    const connection = connectMockSourceCraft("sourcecraft-pat-1234567890");
    expect(connection.connected).toBe(true);
    expect(connection.connectedAt).not.toBeNull();

    disconnectMockSourceCraft();
    expect(mockConnection().connected).toBe(false);
  });
});
