/**
 * Отложенный поиск: запрос уходит, когда в наборе пауза. Новый ввод отменяет прежний таймер,
 * а смена поиска извне — сброс фильтров, «Назад», ссылка — отменяет его совсем: такой ввод
 * устарел. Свой же отправленный поиск, вернувшийся через адрес, внешней сменой не считается,
 * поэтому не стирает то, что человек успел допечатать.
 */
export interface SearchDebounce {
  /** Человек изменил текст в поле. */
  input(text: string): void;
  /** Поиск в адресе стал таким; true — это смена извне и черновик в поле нужно заменить. */
  sync(value: string): boolean;
  /** Отменить ожидающий поиск: поле убрали со страницы. */
  cancel(): void;
}

export function createSearchDebounce(
  initial: string,
  commit: (value: string) => void,
  delayMs = 300,
): SearchDebounce {
  let committed = initial;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function cancel(): void {
    if (timer !== undefined) {
      clearTimeout(timer);
      timer = undefined;
    }
  }

  return {
    input(text) {
      cancel();
      const next = text.trim();
      // Вернули текст к уже отправленному — искать заново нечего.
      if (next === committed) return;
      timer = setTimeout(() => {
        timer = undefined;
        committed = next;
        commit(next);
      }, delayMs);
    },
    sync(value) {
      if (value === committed) return false;
      cancel();
      committed = value;
      return true;
    },
    cancel,
  };
}
