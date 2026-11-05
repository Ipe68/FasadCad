"""Окна программы (tkinter, крупный шрифт):
  ask        - "Исходные данные" перед построением: объект, система и параметры каждого витража;
  ask_stocks - "Хлысты" перед раскроем: длина хлыста по каждому профилю.

Значения витража хранятся в блоке ФАСАД_КОНСТРУКЦИЯ в чертеже - окно только помогает их заполнить.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .constructions import ATTRS
from .facade import formula_layers
from .lora_link import (beams_with_fixing, fixing_holes, fixing_warning_text, heavy_thickness_hint,
                        norm_art, to_float, wrap_beams_fixing, wrap_kits, wrap_profiles)

FONT = ("Arial", 14)
NOT_COPIED = ("ИМЯ", "КОЛИЧЕСТВО")       # при "применить ко всем" не копируются
SEP = "  —  "                            # "значение  —  расшифровка" в списках


def _root(title: str) -> tk.Tk:
    root = tk.Tk()
    root.title(title)
    root.option_add("*Font", FONT)
    style = ttk.Style(root)
    style.configure(".", font=FONT)
    style.configure("TButton", padding=8)
    root.option_add("*TCombobox*Listbox.font", FONT)
    return root


def _show(root: tk.Tk):
    root.update_idletasks()
    root.minsize(root.winfo_width(), root.winfo_height())
    root.attributes("-topmost", True)           # поверх AutoCAD
    root.after(500, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.mainloop()


def confirm_project_overwrite(name: str) -> bool:
    """Подтвердить повторное сохранение одноимённого проекта."""
    root = tk.Tk()
    root.withdraw()
    try:
        return messagebox.askyesno(
            "Перезаписать проект?",
            f"Проект {name} есть в базе проектов, перезаписать?",
            parent=root,
        )
    finally:
        root.destroy()


def ask(vitrages: list[dict], top: dict, choices: dict, kit_ok=None, statics=None, statics_geom=None
       ) -> tuple[list[dict], dict] | None:
    """vitrages: [{"подпись": "X 0..3600", "атрибуты": {ТЕГ: значение}}]; top: {"объект", "система"}.
    choices: ТЕГ -> список значений, или функция(толщина) -> [(значение, расшифровка)] (варианты по толщине);
    "системы" - список систем. kit_ok(заполнение, вариант) -> текст ошибки или "".
    statics: объект fasad.lora_link.Statics - чекбокс "Считать статику" и фильтры по расчёту Lora.
    statics_geom: геометрия витражей [cell_geometry(...) | None] - передаётся в Lora с размерами.
    Возвращает (атрибуты по витражам, top) или None - отмена."""
    root = _root("Фасад - исходные данные")
    data = [dict(v["атрибуты"]) for v in vitrages]
    result: dict = {}
    orig_choices = dict(choices)

    # ---------- объект ----------
    frm_top = ttk.LabelFrame(root, text="Объект", padding=10)
    frm_top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 5))
    v_obj, v_sys = tk.StringVar(value=top["объект"]), tk.StringVar(value=top["система"])
    ttk.Label(frm_top, text="Название (имя файла Excel)").grid(row=0, column=0, sticky="w")
    ttk.Entry(frm_top, textvariable=v_obj, width=40).grid(row=0, column=1, sticky="w", padx=8)
    ttk.Label(frm_top, text="Система").grid(row=0, column=2, sticky="w", padx=(20, 0))
    sys_combo = ttk.Combobox(frm_top, textvariable=v_sys, values=choices.get("системы", []), width=14,
                             state="readonly")
    sys_combo.grid(row=0, column=3, sticky="w", padx=8)

    # ---------- статика (Lora) ----------
    if statics is not None:
        v_stat = tk.BooleanVar(value=False)
        chk_stat = ttk.Checkbutton(frm_top, text="Считать статику", variable=v_stat)
        chk_stat.grid(row=1, column=0, sticky="w", pady=(10, 0))
        btn_stat = ttk.Button(frm_top, text="Расчёт нагрузок", state="disabled",
                              command=lambda: run_statics())
        btn_stat.grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))
        lbl_stat = ttk.Label(frm_top, text="", wraplength=1150)
        lbl_stat.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

    # ---------- список витражей ----------
    frm_list = ttk.LabelFrame(root, text="Витражи", padding=10)
    frm_list.grid(row=1, column=0, sticky="ns", padx=(10, 5), pady=5)
    lb = tk.Listbox(frm_list, width=18, height=16, exportselection=False, font=FONT)
    lb.pack(fill="y", expand=True)

    # ---------- поля витража ----------
    frm = ttk.LabelFrame(root, text="Витраж", padding=10)
    frm.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=5)
    wide = ("СТОЙКА", "РИГЕЛЬ", "ФОРМУЛА", "ВАРИАНТ", "ПЕРИМЕТР_ВАРИАНТ")   # длинные - во всю ширину внизу
    fields = [a for a in ATTRS if a[0] != "СИСТЕМА" and a[0] not in wide]
    half = (len(fields) + 1) // 2
    vars_: dict[str, tk.StringVar] = {}
    combos: dict[str, ttk.Combobox] = {}
    for k, (tag, label, _, _) in enumerate(fields):
        col, row = (0, k) if k < half else (2, k - half)
        ttk.Label(frm, text=label).grid(row=row, column=col, sticky="w", padx=(0 if col == 0 else 25, 6), pady=3)
        var = tk.StringVar()
        vals = choices.get(tag)
        if vals:
            w = ttk.Combobox(frm, textvariable=var, values=vals, width=16)
        else:
            w = ttk.Entry(frm, textvariable=var, width=18)
        w.grid(row=row, column=col + 1, sticky="w", pady=3)
        vars_[tag] = var
    for k, tag in enumerate(wide):
        label = next(a[1] for a in ATTRS if a[0] == tag)
        ttk.Label(frm, text=label).grid(row=half + k, column=0, sticky="w", pady=(10 if k == 0 else 3, 3))
        vars_[tag] = tk.StringVar()
        if callable(choices.get(tag)):
            w = ttk.Combobox(frm, textvariable=vars_[tag], width=78, state="readonly")
            combos[tag] = w
        else:
            w = ttk.Entry(frm, textvariable=vars_[tag], width=80)
        w.grid(row=half + k, column=1, columnspan=3, sticky="ew", pady=(10 if k == 0 else 3, 3))
    current = {"i": None}

    def refresh_options(*_):
        """Варианты комплектации и доборных - только для текущей толщины, с расшифровкой."""
        thick = vars_["ЗАПОЛНЕНИЕ"].get()
        for tag, cb in combos.items():
            opts = choices[tag](thick)
            items = [f"{v}{SEP}{d}" for v, d in opts]
            cb.configure(values=items)
            cur = vars_[tag].get().split(SEP)[0]
            match = next((it for it, (v, _) in zip(items, opts) if v == cur), None)
            vars_[tag].set(match or (items[0] if items else ""))

    vars_["ЗАПОЛНЕНИЕ"].trace_add("write", refresh_options)

    def value(tag):
        return vars_[tag].get().split(SEP)[0].strip()

    def save_current():
        i = current["i"]
        if i is not None:
            for tag in vars_:
                data[i][tag] = value(tag)
            lb.delete(i)
            lb.insert(i, _caption(data[i]))

    def show(i):
        current["i"] = i
        for tag, var in vars_.items():
            var.set(data[i].get(tag, next(d for t, _, d, _ in ATTRS if t == tag)))
        refresh_options()
        frm.configure(text=f"Витраж {vitrages[i]['подпись']}")

    def on_select(_=None):
        sel = lb.curselection()
        if not sel or sel[0] == current["i"]:
            return
        save_current()
        lb.selection_set(sel[0])
        show(sel[0])

    for d in data:
        lb.insert("end", _caption(d))
    lb.bind("<<ListboxSelect>>", on_select)

    # ---------- кнопки ----------
    frm_b = ttk.Frame(root, padding=10)
    frm_b.grid(row=2, column=0, columnspan=2, sticky="ew")

    def apply_all():
        save_current()
        i = current["i"]
        if i is None or not messagebox.askyesno("Применить ко всем",
                                                f"Скопировать данные {data[i].get('ИМЯ', '')} во все витражи "
                                                "(кроме марки и количества)?", parent=root):
            return
        for j, d in enumerate(data):
            if j != i:
                d.update({t: v for t, v in data[i].items() if t not in NOT_COPIED})
        lb.delete(0, "end")
        for d in data:
            lb.insert("end", _caption(d))
        lb.selection_set(i)

    def build():
        save_current()
        errs = []
        names = [d.get("ИМЯ", "") for d in data]
        for d in data:
            errs += [f"{d.get('ИМЯ', '?')}: {e}" for e in _check(d, kit_ok)]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            errs.append("Марки повторяются: " + ", ".join(dup))
        if not v_obj.get().strip():
            errs.append("Не задано название объекта")
        if errs:
            messagebox.showerror("Проверьте исходные данные", "\n".join(errs), parent=root)
            return
        result.update(top={"объект": v_obj.get().strip(), "система": v_sys.get().strip()})
        root.destroy()

    btn_apply = ttk.Button(frm_b, text="Применить ко всем витражам", command=apply_all)
    btn_apply.pack(side="left")
    ttk.Button(frm_b, text="Отмена", command=root.destroy).pack(side="right")
    btn_build = ttk.Button(frm_b, text="Построить", command=build)
    btn_build.pack(side="right", padx=10)

    # ---------- статика: блокировка полей, запуск Lora, фильтры списков ----------
    if statics is not None:
        applying = {"on": False}
        note_fix = {"text": ""}

        def set_locked(on: bool):
            """Галка 'Считать статику': активны только название объекта и чекбокс с кнопкой."""
            state = "disabled" if on else "normal"
            for w in frm.winfo_children():
                if isinstance(w, (ttk.Entry, ttk.Combobox)):
                    w.configure(state=state)
            for cb in combos.values():
                cb.configure(state="disabled" if on else "readonly")
            lb.configure(state=state)
            sys_combo.configure(state="disabled" if on else "readonly")
            btn_apply.configure(state=state)
            btn_build.configure(state=state)
            btn_stat.configure(state="normal" if on else "disabled")

        def restore_choices():
            choices.clear()
            choices.update(orig_choices)
            note_fix["text"] = ""
            lbl_stat.configure(text="")
            refresh_options()

        def _prof(p) -> str:
            if not p or not p.get("артикул"):
                return ""
            d = to_float(p.get("глубина"))
            return f'{p["артикул"]} {d:g} мм' if d is not None else str(p["артикул"])

        def statics_label(res) -> str:
            st, rg = res.get("стойка") or {}, res.get("ригель") or {}
            bits = [f"{w} {s}" for w, p in (("стойка", st), ("ригель", rg))
                    if (s := _prof(p))]
            if (wt := to_float(res.get("вес_стекла_кг"))) is not None:
                bits.append(f"стекло {wt:g} кг")
            if not bits:
                return ""
            txt = "Статика: " + ", ".join(bits)
            if not (st.get("ok", True) and rg.get("ok", True)):
                txt += "  — ПРОВЕРИТЬ: выбранный профиль не проходит по нагрузке"
            return txt

        def apply_statics(res):
            """Фильтры по результату Лоры: стойки/ригели не уже выбранных, варианты по весу стекла,
            ригели - только с закладной под требуемые отверстия (Лора смотрит только прочность)."""
            note_fix["text"] = ""
            st, rg = res.get("стойка") or {}, res.get("ригель") or {}
            if (d := to_float(st.get("глубина"))):
                choices["СТОЙКА"] = wrap_profiles(orig_choices["СТОЙКА"], d, statics)
            if (d := to_float(rg.get("глубина"))):
                choices["РИГЕЛЬ"] = wrap_profiles(orig_choices["РИГЕЛЬ"], d, statics)
            if (wt := to_float(res.get("вес_стекла_кг"))):
                choices["ВАРИАНТ"] = wrap_kits(orig_choices["ВАРИАНТ"], statics, wt)
                holes = fixing_holes(statics, wt)
                allowed = beams_with_fixing(statics.system, holes) if holes else set()
                all_beams = orig_choices["РИГЕЛЬ"]("0")
                if allowed and any(norm_art(v) not in allowed for v, _ in all_beams):
                    choices["РИГЕЛЬ"] = wrap_beams_fixing(choices["РИГЕЛЬ"], allowed)
                    rg_art = rg.get("артикул")
                    if rg_art and norm_art(rg_art) not in allowed:
                        note_fix["text"] = (f"Ригель {rg_art} из Лоры не применён: нет закладной "
                                            f"с отверстиями \"{holes}\" (стекло {wt:g} кг)")
            refresh_options()
            for tag, p in (("СТОЙКА", st), ("РИГЕЛЬ", rg)):
                art = p.get("артикул")
                if not art or tag not in combos:
                    continue
                items = list(combos[tag].cget("values"))
                cur = next((it for it in items if norm_art(it.split(SEP)[0].strip()) == norm_art(art)),
                           None)
                if cur is not None:
                    vars_[tag].set(cur)
            txt = statics_label(res)
            if note_fix["text"]:
                txt = (txt + "   —   " if txt else "") + note_fix["text"]
            lbl_stat.configure(text=txt, foreground="#C00000" if "ПРОВЕРИТЬ" in txt or note_fix["text"]
                               else "#1F6B2E")

        def glass_mm():
            """Сумма толщин стёкол пакета (в Лоре - 'Суммарная толщина стекла'), мм:
            6-16-6-16-6 -> 18. Без формулы - None (в Лоре останется своё значение)."""
            f = vars_["ФОРМУЛА"].get().strip()
            if not f:
                return None
            try:
                layers = formula_layers(f)
            except ValueError:
                return None
            return sum(t for kind, t, _ in layers if kind == "стекло") or None

        def run_statics():
            i = current["i"]
            g = statics_geom[i] if statics_geom is not None and i is not None and i < len(statics_geom) else None
            try:
                res = statics.run(v_obj.get().strip(), glass_mm(), g)
            except RuntimeError as e:
                messagebox.showerror("Статика", str(e), parent=root)
                return
            applying["on"] = True
            try:
                v_stat.set(False)               # галка снимается - окно снова активно
            finally:
                applying["on"] = False
            set_locked(False)                   # трейс подавлен выше - разблокируем явно
            if res is None:
                messagebox.showwarning("Статика", "Результат не получен:\n"
                                      "в Лоре не выполнен расчёт или окно закрыто без экспорта.",
                                       parent=root)
                return
            apply_statics(res)
            messagebox.showinfo("Статика", statics_label(res) or "Результат принят.", parent=root)
            msg_fix = fixing_warning_text(statics, res)
            if msg_fix:
                messagebox.showwarning("Статика - крепление ригеля", msg_fix, parent=root)
            hint = heavy_thickness_hint(statics, vars_["ЗАПОЛНЕНИЕ"].get(), res.get("вес_стекла_кг"))
            if hint:
                messagebox.showwarning("Статика - толщина пакета", hint, parent=root)

        def on_stat_toggle(*_):
            if applying["on"]:
                return
            on = bool(v_stat.get())
            set_locked(on)
            if not on:
                restore_choices()

        v_stat.trace_add("write", on_stat_toggle)
        saved_res = statics.read()               # проект уже считался - фильтры сразу
        if saved_res:
            apply_statics(saved_res)

    root.update_idletasks()
    avail_h = root.winfo_screenheight() - 100               # заголовок + панель задач
    while root.winfo_reqheight() > avail_h and int(lb.cget("height")) > 6:
        lb.configure(height=int(lb.cget("height")) - 2)     # список витражей сжимаем первым
        root.update_idletasks()

    if vitrages:
        lb.selection_set(0)
        show(0)
    _show(root)
    if "top" not in result:
        return None
    for d in data:
        d["СИСТЕМА"] = result["top"]["система"]
    return data, result["top"]


def ask_stocks(rows: list[tuple[str, str, float]], lengths: dict[str, float], default: float = 6000
               ) -> dict[str, float] | None:
    """Окно "Хлысты": rows - (артикул, наименование, всего м), lengths - прошлые длины.
    Возвращает {артикул: длина хлыста} или None - отмена (Excel не делать)."""
    root = _root("Фасад - хлысты для раскроя")
    frm = ttk.Frame(root, padding=15)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=f"Длина хлыста по каждому профилю, мм (по умолчанию {default:g})").grid(
        row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
    for c, h in enumerate(("Артикул", "Наименование", "Всего, м", "Хлыст, мм")):
        ttk.Label(frm, text=h, font=("Arial", 14, "bold")).grid(row=1, column=c, sticky="w", padx=8)
    vars_ = {}
    for r, (art, name, metres) in enumerate(rows, 2):
        ttk.Label(frm, text=art).grid(row=r, column=0, sticky="w", padx=8, pady=2)
        ttk.Label(frm, text=name).grid(row=r, column=1, sticky="w", padx=8, pady=2)
        ttk.Label(frm, text=f"{metres:.1f}").grid(row=r, column=2, sticky="e", padx=8, pady=2)
        vars_[art] = tk.StringVar(value=f"{lengths.get(art, default):g}")
        ttk.Entry(frm, textvariable=vars_[art], width=8).grid(row=r, column=3, sticky="w", padx=8, pady=2)
    result = {}

    def ok():
        bad = []
        for art, v in vars_.items():
            try:
                result[art] = float(v.get().replace(",", "."))
            except ValueError:
                bad.append(art)
        if bad:
            result.clear()
            messagebox.showerror("Хлысты", "Не число у: " + ", ".join(bad), parent=root)
            return
        result["_ok"] = 1
        root.destroy()

    frm_b = ttk.Frame(root, padding=10)
    frm_b.pack(fill="x")
    ttk.Button(frm_b, text="Отмена (без Excel)", command=root.destroy).pack(side="right")
    ttk.Button(frm_b, text="Раскрой и Excel", command=ok).pack(side="right", padx=10)
    _show(root)
    if not result.pop("_ok", None):
        return None
    return result


def report(title: str, lines: list[str], warnings: list[str]):
    """Окно "Готово": что построено и что проверить (крупно, предупреждения - красным)."""
    root = _root(title)
    frm = ttk.Frame(root, padding=15)
    frm.pack(fill="both", expand=True)
    for s in lines:
        ttk.Label(frm, text=s).pack(anchor="w")
    if warnings:
        ttk.Label(frm, text="\nПроверить:", font=("Arial", 14, "bold"), foreground="#C00000").pack(anchor="w")
        box_h = max(4, min(18, len(warnings) * 2 + 1, (root.winfo_screenheight() - 300) // 34))
        box = tk.Text(frm, width=100, height=box_h, wrap="word", font=FONT,
                      foreground="#C00000")
        box.insert("end", "\n".join(f"• {w}" for w in warnings))
        box.configure(state="disabled")
        box.pack(fill="both", expand=True)
    ttk.Button(root, text="OK", command=root.destroy).pack(pady=10)
    _show(root)


def _caption(d: dict) -> str:
    return f'{d.get("ИМЯ", "?")} x{d.get("КОЛИЧЕСТВО", "1")}'


def _num(s: str) -> float:
    return float(str(s).replace(",", ".").strip())


def _check(d: dict, kit_ok) -> list[str]:
    errs = []
    for tag in ("КОЛИЧЕСТВО", "ЗАПОЛНЕНИЕ", "ВЫПУСК_НИЗ", "ВЫПУСК_ВЕРХ", "ЗАКЛАДНАЯ_НИЗ", "ЗАКЛАДНАЯ_ВЕРХ",
                "ОТМЕТКА_НИЗ"):
        try:
            _num(d.get(tag, "0") or "0")
        except ValueError:
            errs.append(f"{tag.lower().replace('_', ' ')}: не число \"{d.get(tag)}\"")
    if d.get("ФОРМУЛА"):
        try:
            total = sum(t for _, t, _ in formula_layers(d["ФОРМУЛА"]))
            if abs(total - _num(d.get("ЗАПОЛНЕНИЕ", "0") or "0")) > 1:
                errs.append(f"формула {d['ФОРМУЛА']} - это {total:g} мм, а толщина заполнения "
                            f"{d.get('ЗАПОЛНЕНИЕ')} мм")
        except ValueError as e:
            errs.append(str(e))
    if kit_ok and not errs:
        e = kit_ok(d)
        if e:
            errs.append(e)
    return errs
