# -*- coding: utf-8 -*-
"""
Ревизия папки проекта: какие файлы нужны программе, а какие похожи на мусор.

Запуск (из папки проекта или с указанием пути):
    py audit_project.py                      -> отчёт, НИЧЕГО не меняет
    py audit_project.py C:\\путь\\к\\проекту   -> то же для другой папки
    py audit_project.py --entry main.py      -> точка входа (по умолчанию main.py)
    py audit_project.py --move               -> перенести «мусор» и «не используется»
                                                 в папку _к_удалению (обратимо)

Скрипт только читает файлы (разбирает .py через ast, программу не запускает).
Отчёт сохраняется в audit_report.txt рядом со скриптом.
"""
import ast, os, re, sys, shutil, fnmatch, argparse, time

SKIP_DIRS = {'.git', '.hg', '.svn', 'venv', '.venv', 'env', '.env', 'node_modules',
             '.idea', '.vscode', '_к_удалению'}
JUNK_DIRS = {'__pycache__', '.pytest_cache', '.mypy_cache', 'build'}   # build/ — артефакт PyInstaller
JUNK_NAME = re.compile(r'(\.pyc|\.pyo|\.bak|\.orig|\.tmp|\.temp|\.log|\.swp|~)$|'
                       r'^(thumbs\.db|desktop\.ini|\.ds_store)$|^~\$|'
                       r'(копия|copy|_old|_bak|_backup|_back|\(\d+\)|_\d+)\.[^.]+$|'
                       r' - копия| - copy', re.I)
KEEP_NAMES = {'requirements.txt', 'readme.md', 'readme.txt', 'license', 'license.txt',
              '.gitignore', 'pyproject.toml', 'setup.py', 'setup.cfg', 'pytest.ini'}
BUILD_EXT = {'.spec', '.iss', '.bat', '.cmd', '.ps1', '.sh', '.ico'}
DYN_CALLS = {'listdir', 'iglob', 'walk', 'iterdir', 'scandir', 'rglob'}


def human(n):
    for u in ('Б', 'КБ', 'МБ', 'ГБ'):
        if n < 1024 or u == 'ГБ':
            return f'{n:.0f} {u}' if u == 'Б' else f'{n:.1f} {u}'
        n /= 1024


def norm(p):
    return p.replace('\\', '/').strip().lower()


def scan(root):
    files, dirs = {}, set()
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        rel_d = os.path.relpath(dp, root)
        if rel_d != '.':
            dirs.add(rel_d.replace('\\', '/'))
        for f in fn:
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, root).replace('\\', '/')
            try:
                st = os.stat(full)
                files[rel] = (st.st_size, st.st_mtime)
            except OSError:
                pass
    return files, dirs


def parse_py(path):
    try:
        with open(path, 'rb') as fh:
            return ast.parse(fh.read(), filename=path)
    except (SyntaxError, ValueError, OSError):
        return None


def resolve_module(root, mod, pyset):
    """'a.b.c' -> относительный путь локального файла или None"""
    parts = mod.split('.')
    cands = ['/'.join(parts) + '.py', '/'.join(parts) + '/__init__.py']
    for c in cands:
        if c in pyset:
            return c
    return None


def analyse(root, files, entries):
    pyset = {f for f in files if f.endswith('.py')}
    reachable, queue, consts, dyn = set(), [], set(), []
    for e in entries:
        e = e.replace('\\', '/')
        if e in pyset:
            queue.append(e)
        else:
            print(f'! Точка входа {e} не найдена в {root}')
    while queue:
        cur = queue.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        tree = parse_py(os.path.join(root, cur))
        if tree is None:
            continue
        pkg = os.path.dirname(cur).replace('/', '.')
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    up = pkg.split('.') if pkg else []
                    up = up[:len(up) - (node.level - 1)] if node.level > 1 else up
                    base = '.'.join([p for p in up + [base] if p])
                mods = [base] + [f'{base}.{a.name}' if base else a.name for a in node.names]
            for m in mods:
                r = resolve_module(root, m, pyset)
                if r:
                    queue.append(r)
                    # родительские пакеты
                    d = os.path.dirname(r)
                    while d:
                        init = d + '/__init__.py'
                        if init in pyset:
                            queue.append(init)
                        d = os.path.dirname(d)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = node.value
                if 0 < len(s) < 300:
                    consts.add(s)
                    # строки-имена модулей (importlib.import_module("x"))
                    if re.fullmatch(r'[A-Za-z_][\w.]*', s):
                        r = resolve_module(root, s, pyset)
                        if r:
                            queue.append(r)
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', '')
                if name in DYN_CALLS:
                    dyn.append(f'{cur}:{node.lineno}  {name}()')
    return reachable, consts, dyn


def classify(root, files, dirs, reachable, consts, dyn):
    cl = {k: [] for k in ('need_py', 'need_data', 'maybe', 'build', 'keep', 'junk', 'unused_py', 'unused_data')}
    cnorm = {norm(c) for c in consts}
    cbase = {c.split('/')[-1] for c in cnorm}
    dir_names = {d.split('/')[-1].lower(): d for d in dirs}
    ref_dirs = {dir_names[c.strip('/').split('/')[-1]] for c in cnorm
                if c.strip('/').split('/')[-1] in dir_names}

    def is_direct(low, base):
        return (base in cbase or low in cnorm or
                any(c.endswith('/' + low) or low.endswith('/' + c) for c in cnorm if '/' in c))

    HARD = re.compile(r'(\.pyc|\.pyo|\.swp|~)$|^(thumbs\.db|desktop\.ini|\.ds_store)$|^~\$', re.I)
    direct_files = set()
    for rel in files:
        low = rel.lower(); base = low.split('/')[-1]
        if not rel.endswith('.py') and is_direct(low, base):
            direct_files.add(rel)
    # папки, где хотя бы один файл упомянут в коде напрямую = «папки ресурсов» (UI, images ...)
    res_dirs = {d for d in ref_dirs if any(f.startswith(d + '/') for f in direct_files)}

    for rel, (size, mt) in sorted(files.items()):
        low = rel.lower(); base = low.split('/')[-1]; stem, ext = os.path.splitext(base)
        parts = rel.split('/')
        if rel.endswith('.py'):
            (cl['need_py'] if rel in reachable else cl['unused_py']).append((rel, size, ''))
            continue
        if base in KEEP_NAMES:
            cl['keep'].append((rel, size, '')); continue
        # заведомый мусор (кэш, системные файлы)
        if any(p in JUNK_DIRS for p in parts[:-1]) or HARD.search(base):
            cl['junk'].append((rel, size, 'кэш / служебный файл')); continue
        # файл, имя которого прямо упомянуто в коде — нужен, даже если имя похоже на копию
        if rel in direct_files:
            note = 'имя файла встречается в коде'
            if JUNK_NAME.search(base): note += ' (имя похоже на копию, но код его использует!)'
            cl['need_data'].append((rel, size, note)); continue
        if JUNK_NAME.search(base):
            cl['junk'].append((rel, size, 'похоже на копию / резервный / временный файл')); continue
        if ext in BUILD_EXT:
            cl['build'].append((rel, size, 'файл сборки / запуска')); continue
        in_res = [d for d in res_dirs if rel.startswith(d + '/')]
        if in_res:
            cl['unused_data'].append((rel, size, f'лежит в папке ресурсов «{in_res[0]}», но код его не упоминает')); continue
        in_ref = [d for d in ref_dirs if rel.startswith(d + '/')]
        if in_ref:
            cl['maybe'].append((rel, size, f'папка «{in_ref[0]}» используется кодом — вероятно, данные программы')); continue
        if stem in cbase:
            cl['maybe'].append((rel, size, 'имя без расширения встречается в коде')); continue
        cl['unused_data'].append((rel, size, 'код нигде не упоминает'))
    return cl


def write_report(root, cl, dyn, dirs, files, entries, out):
    L = []
    P = L.append
    P(f'РЕВИЗИЯ ПРОЕКТА: {root}')
    P(f'Точки входа: {", ".join(entries)}    Файлов: {len(files)}    Папок: {len(dirs)}')
    P('=' * 78)
    titles = [
        ('need_py', '1. НУЖНЫ — .py, которые подключаются из main.py (напрямую или цепочкой)'),
        ('need_data', '2. НУЖНЫ — файлы данных, имя которых прямо встречается в коде (.ui, картинки…)'),
        ('maybe', '3. СКОРЕЕ НУЖНЫ — не удалять, пока не проверите (данные программы, маски…)'),
        ('build', '4. СБОРКА / ЗАПУСК — .spec, .bat, значки: нужны, если вы собираете exe'),
        ('keep', '5. ОСТАВИТЬ — requirements.txt, README и т.п.'),
        ('junk', '6. МУСОР — кэш, копии, резервные и временные файлы (можно убирать)'),
        ('unused_py', '7. НЕ ИСПОЛЬЗУЮТСЯ — .py, которые main.py не подключает (черновики, старые версии?)'),
        ('unused_data', '8. НЕ ИСПОЛЬЗУЮТСЯ — файлы, имя которых нигде не встречается в коде'),
    ]
    for key, title in titles:
        items = cl[key]
        P(''); P(title); P('-' * 78)
        if not items:
            P('  (нет)'); continue
        tot = sum(s for _, s, _ in items)
        for rel, size, why in items:
            P(f'  {rel}   [{human(size)}]' + (f'   — {why}' if why else ''))
        P(f'  Итого: {len(items)} шт., {human(tot)}')
    P(''); P('=' * 78)
    if dyn:
        P('ВНИМАНИЕ: в нужных файлах есть динамический поиск файлов (listdir/glob/walk):')
        for d in dyn[:30]: P('   ' + d)
        P('  Программа может подхватывать файлы из папок по маске — пункты 7–8 для таких папок проверьте вручную.')
        P('')
    P('Скрипт ничего не удаляет. Рекомендуемый порядок:')
    P('  1) py audit_project.py --move   (перенесёт пункты 6, 7, 8 в папку _к_удалению)')
    P('  2) запустите программу, пройдитесь по всем окнам и функциям;')
    P('  3) если всё работает — удалите _к_удалению; если чего-то не хватает — верните файл на место.')
    P('Что скрипт НЕ видит: файлы, имена которых собираются из переменных (например, f"{art}.json"),')
    P('и файлы, нужные только при сборке exe (PyInstaller datas) — они попадают в пункты 3–4 или 8.')
    text = '\n'.join(L)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?', default='.')
    ap.add_argument('--entry', nargs='+', default=['main.py'])
    ap.add_argument('--move', action='store_true')
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    files, dirs = scan(root)
    reachable, consts, dyn = analyse(root, files, a.entry)
    cl = classify(root, files, dirs, reachable, consts, dyn)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'audit_report.txt')
    text = write_report(root, cl, dyn, dirs, files, a.entry, out)
    print(text)
    print(f'\nОтчёт сохранён: {out}')
    if a.move:
        dest = os.path.join(root, '_к_удалению')
        n = 0
        for key in ('junk', 'unused_py', 'unused_data'):
            for rel, _, _ in cl[key]:
                if os.path.basename(rel) == 'audit_project.py':
                    continue
                src = os.path.join(root, rel); dst = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    shutil.move(src, dst); n += 1
                except OSError as e:
                    print('  не удалось перенести', rel, e)
        print(f'Перенесено файлов: {n} -> {dest}')


if __name__ == '__main__':
    main()
