;;; Команды программы Fasad в AutoCAD (вместо запуска из PowerShell).
;;;   ФАСАД           (FASAD)           - построить фасад и сохранить проект
;;;   ФАСАДБАЗА       (FASADBAZA)       - пересобрать базу узлов из каталога
;;; Программа открывается в отдельном окне; когда закончит - окно ждёт нажатия клавиши,
;;; чтобы можно было прочитать замечания.
;;; Загрузка навсегда: APPLOAD -> "Автозагрузка" (Startup Suite) -> Добавить -> этот файл.

(setq *fasad-dir* "C:\\cad\\Fasad"
      *fasad-dispatch* "C:\\Users\\ipe_A\\autocad-mcp\\lisp-code\\mcp_dispatch.lsp")

(defun fasad-run (script)
  ;; связь программы с AutoCAD - через mcp_dispatch.lsp: загрузить, если ещё не загружен
  (if (and (not c:mcp-dispatch) (findfile *fasad-dispatch*))
    (load *fasad-dispatch*))
  (cond
    ((not c:mcp-dispatch)
     (alert (strcat "Не найден " *fasad-dispatch* "\nПрограмма не сможет связаться с AutoCAD.")))
    (t
     (startapp "cmd.exe" (strcat "/c cd /d \"" *fasad-dir* "\" && py \"" script "\" & pause"))
     (princ (strcat "\nЗапущено: " script " - смотрите отдельное окно."))))
  (princ))

(defun c:ФАСАД () (fasad-run "New project.py"))
(defun c:FASAD () (c:ФАСАД))
(defun c:ФАСАДБАЗА () (fasad-run "Собрать_базу.py"))
(defun c:FASADBAZA () (c:ФАСАДБАЗА))

(princ "\nFasad: команды ФАСАД (FASAD) и ФАСАДБАЗА (FASADBAZA) загружены.")
(princ)
