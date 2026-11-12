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

;;; ЗАМЕНАСТОЙКИ (REPLACEPOST) / ЗАМЕНАРИГЕЛЯ (REPLACEBEAM) - замена профиля по маркам:
;;; выбрать полилинии стоек или ригелей на фасаде (Enter - конец выбора). Выбранные полилинии
;;; подсвечиваются, как в КОПИРОВАТЬ. Дальше в отдельном окне - одно окно с допустимыми профилями
;;; (по самой нагруженной марке), правка пишется в таблицу профилей, чертёж перестраивается.
(defun _fasad-collect-profiles (kind / ent ed f n xs ys hl ps)
  (setq ps (getvar "PICKSTYLE"))
  (setvar "PICKSTYLE" 0)                   ; выбор по одной полилинии, группы не мешают
  (setq f (open "C:/temp/fasad_replace.txt" "w") n 0 hl nil)
  (write-line (strcat "kind:" kind) f)
  (while (setq ent (entsel "\nПолилиния профиля на фасаде (Enter - закончить выбор): "))
    (setq ed (entget (car ent)))
    (if (/= "LWPOLYLINE" (cdr (assoc 0 ed)))
      (princ "\nПропуск: это не полилиния.")
      (progn
        (redraw (car ent) 3)                 ; подсветить выбранное
        (setq hl (cons (car ent) hl))
        (setq xs nil ys nil)
        (foreach g ed
          (if (= 10 (car g))
            (progn (setq xs (cons (cadr g) xs)) (setq ys (cons (caddr g) ys)))))
        (write-line (strcat (cdr (assoc 8 ed)) ";"
                            (rtos (apply 'min xs) 2 2) ";" (rtos (apply 'min ys) 2 2) ";"
                            (rtos (apply 'max xs) 2 2) ";" (rtos (apply 'max ys) 2 2)) f)
        (setq n (1+ n)))))
  (close f)
  (setvar "PICKSTYLE" ps)                  ; вернуть как было
  (foreach e hl (redraw e 4))                ; снять подсветку
  (if (> n 0)
    (fasad-run "Replace profile.py")
    (princ "\nНичего не выбрано."))
  (princ))

(defun c:ЗАМЕНАСТОЙКИ () (_fasad-collect-profiles "стойка"))
(defun c:REPLACEPOST () (c:ЗАМЕНАСТОЙКИ))
(defun c:ЗАМЕНАРИГЕЛЯ () (_fasad-collect-profiles "ригель"))
(defun c:REPLACEBEAM () (c:ЗАМЕНАРИГЕЛЯ))

;;; ДОБАВИТЬСЕЧЕНИЕ (ADDSECTION) - провести линию (две точки): вертикальная - сечение через пролёт,
;;; горизонтальная - по всем стойкам на высоте линии. Линия хранится на слое FASAD_СЕЧЕНИЯ
;;; (программа его не стирает), добавленные сечения В-В, Г-Г... строятся при каждом построении.
(defun _fasad-section-layer (/ lay lt lo ed)
  (setq lay "FASAD_СЕЧЕНИЯ" lt "СПДС штриховая")
  (if (not (tblsearch "LAYER" lay))
    (entmake (list (cons 0 "LAYER") (cons 100 "AcDbSymbolTableRecord") (cons 100 "AcDbLayerTableRecord")
                   (cons 2 lay) (cons 70 0) (cons 62 253) (cons 290 0))))   ; 290 0 - не печатать
  ;; свойства слоя: цвет 253 (серый), вес 0, тип СПДС штриховая (если такой тип есть в чертеже),
  ;; непечатаемый - линия сечения нужна программе, но на печати не нужна
  (if (setq lo (tblobjname "LAYER" lay))
    (progn
      (setq ed (entget lo))
      (foreach pr (list (cons 62 253) (cons 370 0) (cons 290 0))
        (if (assoc (car pr) ed)
          (entmod (setq ed (subst pr (assoc (car pr) ed) ed)))
          (entmod (setq ed (append ed (list pr))))))
      (if (and (tblsearch "LTYPE" lt) (assoc 6 ed))
        (entmod (setq ed (subst (cons 6 lt) (assoc 6 ed) ed))))))
  lay)

(defun c:ДОБАВИТЬСЕЧЕНИЕ (/ lay p1 p2)
  (setq lay (_fasad-section-layer))
  (if (and (setq p1 (getpoint "\nПервая точка линии сечения: "))
           (setq p2 (getpoint p1 "\nВторая точка: ")))
    (progn
      (entmake (list (cons 0 "LINE") (cons 8 lay) (cons 10 p1) (cons 11 p2)))
      (fasad-run "Add section.py"))
    (princ "\nЛиния не задана."))
  (princ))
(defun c:ADDSECTION () (c:ДОБАВИТЬСЕЧЕНИЕ))

(princ "\nFasad: команды ФАСАД (FASAD), ФАСАДБАЗА (FASADBAZA), ЗАМЕНАСТОЙКИ (REPLACEPOST), ЗАМЕНАРИГЕЛЯ (REPLACEBEAM), ДОБАВИТЬСЕЧЕНИЕ (ADDSECTION) загружены.")
(princ)
