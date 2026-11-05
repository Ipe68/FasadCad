"""Построить фасад в активном чертеже и сохранить проект."""

from pathlib import Path

from fasad.acad_link import Acad, AcadError
from fasad.projects import ProjectError, find_project_drawing, project_drawing

HERE = Path(__file__).parent


def main():
    try:
        acad = Acad()
        active_path = Path(acad.drawing_path()).resolve()
        if not active_path.name:
            raise AcadError("В AutoCAD нет активного сохранённого чертежа.")
        name = Path(acad.drawing_name()).stem
        existing = find_project_drawing(name)
        if existing is not None:
            from fasad.dialog import confirm_project_overwrite

            if not confirm_project_overwrite(name):
                print("Отменено.")
                return
            destination = existing
        else:
            destination = project_drawing(name)
        folder = destination.parent

        def save_project():
            folder.mkdir(parents=True, exist_ok=True)
            if active_path == destination.resolve():
                acad.save_drawing()
            else:
                acad.save_as(destination, overwrite=existing is not None)

        from fasad.run import postroit_s_oknom

        postroit_s_oknom(acad, HERE / "systems", folder, on_built=save_project)
    except (AcadError, ProjectError, OSError) as e:
        print("ОШИБКА:", e)


if __name__ == "__main__":
    main()
