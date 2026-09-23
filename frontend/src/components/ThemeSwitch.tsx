import { Display, Moon, Sun } from "@gravity-ui/icons";
import { Icon, SegmentedRadioGroup } from "@gravity-ui/uikit";

import type { ThemePreference } from "../lib/theme";
import { useThemeChoice } from "../theme/ThemeChoice";
import "./ThemeSwitch.css";

const options: Array<{ value: ThemePreference; title: string; icon: typeof Sun }> = [
  { value: "system", title: "Как в системе", icon: Display },
  { value: "light", title: "Светлая тема", icon: Sun },
  { value: "dark", title: "Тёмная тема", icon: Moon },
];

/** Переключатель темы в шапке: три значка вместо выпадающего списка. */
export function ThemeSwitch() {
  const { preference, setPreference } = useThemeChoice();

  return (
    <SegmentedRadioGroup
      className="theme-switch"
      size="m"
      value={preference}
      onUpdate={(value) => setPreference(value as ThemePreference)}
      aria-label="Тема оформления"
    >
      {options.map((option) => (
        <SegmentedRadioGroup.Option key={option.value} value={option.value} title={option.title}>
          <Icon data={option.icon} size={15} />
          <span className="visually-hidden">{option.title}</span>
        </SegmentedRadioGroup.Option>
      ))}
    </SegmentedRadioGroup>
  );
}
