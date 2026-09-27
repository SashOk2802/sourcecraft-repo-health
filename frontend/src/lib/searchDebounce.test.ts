import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSearchDebounce } from "./searchDebounce";

describe("отложенный поиск", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("отправляет поиск после паузы в наборе и только последний текст", () => {
    const commit = vi.fn();
    const search = createSearchDebounce("", commit);
    search.input("k");
    vi.advanceTimersByTime(200);
    search.input("kit ");
    vi.advanceTimersByTime(299);
    expect(commit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(commit).toHaveBeenCalledExactlyOnceWith("kit");
  });

  it("смена поиска извне отменяет ожидающий ввод", () => {
    // Набрали «kit» и сразу нажали «Сбросить фильтры»: старый ввод не должен вернуться.
    const commit = vi.fn();
    const search = createSearchDebounce("", commit);
    search.input("kit");
    expect(search.sync("go")).toBe(true);
    vi.advanceTimersByTime(1000);
    expect(commit).not.toHaveBeenCalled();
  });

  it("свой отправленный поиск не считает сменой извне", () => {
    const commit = vi.fn();
    const search = createSearchDebounce("", commit);
    search.input("kit");
    vi.advanceTimersByTime(300);
    // Адрес вернул «kit» — поле не трогаем: человек мог уже допечатать дальше.
    expect(search.sync("kit")).toBe(false);
  });

  it("не ищет заново, если текст вернули к отправленному", () => {
    const commit = vi.fn();
    const search = createSearchDebounce("kit", commit);
    search.input("kitt");
    search.input("kit");
    vi.advanceTimersByTime(1000);
    expect(commit).not.toHaveBeenCalled();
  });

  it("отмена при уходе со страницы", () => {
    const commit = vi.fn();
    const search = createSearchDebounce("", commit);
    search.input("kit");
    search.cancel();
    vi.advanceTimersByTime(1000);
    expect(commit).not.toHaveBeenCalled();
  });
});
