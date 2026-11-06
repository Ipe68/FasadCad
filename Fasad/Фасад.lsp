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

;;; ЗАМЕНАПРОФИЛЯ - замена профиля по маркам: выбрать полилинии стоек/ригелей на фасаде (Enter - конец),
;;; дальше в отдельном окне - по очереди окна с допустимыми профилями, правка пишется в таблицу
;;; профилей и чертёж перестраивается.
(defun c:ЗАМЕНАПРОФИЛЯ (/ ent ed f n xs ys)
  (setq f (open "C:/temp/fasad_replace.txt" "w") n 0)
  (while (setq ent (entsel "\nПолилиния профиля на фасаде (Enter - закончить выбор): "))
    (setq ed (entget (car ent)))
    (if (/= "LWPOLYLINE" (cdr (assoc 0 ed)))
      (princ "\nПропуск: это не полилиния.")
      (progn
        (setq xs nil ys nil)
        (foreach g ed
          (if (= 10 (car g))
            (progn (setq xs (cons (cadr g) xs)) (setq ys (cons (caddr g) ys)))))
        (write-line (strcat (cdr (assoc 8 ed)) ";"
                            (rtos (apply 'min xs) 2 2) ";" (rtos (apply 'min ys) 2 2) ";"
                            (rtos (apply 'max xs) 2 2) ";" (rtos (apply 'max ys) 2 2)) f)
        (setq n (1+ n)))))
  (close f)
  (if (> n 0)
    (fasad-run "Replace profile.py")
    (princ "\nНичего не выбрано."))
  (princ))
(defun c:REPLACEPROFILE () (c:ЗАМЕНАПРОФИЛЯ))

(princ "\nFasad: команды ФАСАД (FASAD), ФАСАДБАЗА (FASADBAZA), ЗАМЕНАПРОФИЛЯ (REPLACEPROFILE) загружены.")
(princ)
