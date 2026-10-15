"""Проверки модели и загрузки кейсов.

Модель — контракт между человеком и раннером, и большинство её проверок
отрицательные: «этот кейс обязан быть отвергнут». Положительных случаев мало
потому, что хороший кейс — это просто YAML без сюрпризов.
"""

from __future__ import annotations

import pathlib

import pytest

from botkit_farm.case.loader import CaseError, load_dir, load_file
from botkit_farm.case.model import Case

GOOD = """
id: booking-start
title: Стартовое меню
source: ./bots/botkit-bookingbot
steps:
  - send: /start
    expect:
      text_contains: ["Запись"]
"""


def _write(tmp_path: pathlib.Path, text: str, name: str = "c.yaml") -> pathlib.Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ── модель ──────────────────────────────────────────────────────────────────


def test_хороший_кейс_читается(tmp_path):
    case = load_file(_write(tmp_path, GOOD))
    assert case.id == "booking-start"
    assert len(case.steps) == 1


def test_пустое_ожидание_отвергается():
    """Кейс без ожиданий не упадёт, а значит, ничего не проверяет.

    Шаг с пустым expect:{} ловится на уровне самого ожидания, кейс целиком без
    ожиданий — на уровне Case (см. следующий тест).
    """
    with pytest.raises(ValueError, match="ожидание пустое"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Пустое ожидание",
                "source": ".",
                "steps": [{"send": "/start", "expect": {}}],
            }
        )


def test_лишнее_поле_отвергается():
    """Опечатка в поле должна падать, а не молча выключать проверку."""
    with pytest.raises(ValueError):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Опечатка",
                "source": ".",
                "steps": [{"send": "/start", "expect": {"silent": True}}],
                "expct": ["опечатка"],
            }
        )


def test_оба_действия_в_шаге_отвергаются():
    with pytest.raises(ValueError, match="ровно один из send, click, pick"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Оба",
                "source": ".",
                "steps": [{"send": "/start", "click": "Далее", "expect": {"silent": True}}],
            }
        )


def test_ни_одного_действия_отвергается():
    with pytest.raises(ValueError, match="ровно один из send, click, pick"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Пусто",
                "source": ".",
                "steps": [{"expect": {"silent": True}}],
            }
        )


def test_choice_вместе_с_send_отвергается():
    with pytest.raises(ValueError, match="ровно один"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "И choice, и send",
                "source": ".",
                "steps": [
                    {
                        "send": "/start",
                        "choice": [{"click": "Далее"}],
                        "expect": {"silent": True},
                    }
                ],
            }
        )


def test_ветка_choice_с_двумя_действиями_отвергается():
    with pytest.raises(ValueError, match="вариант choice"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Ветка с двумя действиями",
                "source": ".",
                "steps": [
                    {
                        "choice": [{"send": "а", "click": "б"}],
                        "expect": {"silent": True},
                    }
                ],
            }
        )


@pytest.mark.parametrize("repeat", [0, 101])
def test_repeat_вне_границ_отвергается(repeat):
    with pytest.raises(ValueError):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Неверный repeat",
                "source": ".",
                "steps": [{"send": "а", "repeat": repeat, "expect": {"silent": True}}],
            }
        )


def test_label_для_choice_называет_первую_ветку():
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "Choice",
            "source": ".",
            "steps": [{"choice": [{"click": "Купить"}], "expect": {"silent": True}}],
        }
    )
    assert case.steps[0].label() == "выбрать доступное действие (нажать «Купить»)"


def test_equals_и_contains_вместе_отвергаются():
    with pytest.raises(ValueError, match="text_equals и text_contains"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "И то и другое",
                "source": ".",
                "steps": [
                    {"send": "/start", "expect": {"text_equals": "a", "text_contains": ["b"]}}
                ],
            }
        )


def test_ни_одного_ожидания_во_всем_кейсе_отвергается():
    with pytest.raises(ValueError, match="ни на одном шаге"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Пустой кейс",
                "source": ".",
                "steps": [{"send": "/start"}, {"send": "/help"}],
            }
        )


def test_неизвестный_тег_отвергается():
    with pytest.raises(ValueError, match="неизвестные теги"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Тег",
                "source": ".",
                "steps": [{"send": "/start", "expect": {"silent": True}}],
                "tags": ["нетакого"],
            }
        )


def test_сырой_callback_принимается_под_тегом_security():
    """Callback в кейсе — это подделка нажатия; в обычном кейсе ему не место."""
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "IDOR",
            "source": ".",
            "tags": ["security"],
            "steps": [{"callback": "order_cancel", "expect": {"silent": True}}],
        }
    )

    assert case.steps[0].callback == "order_cancel"


def test_сырой_callback_принимается_под_тегом_edge():
    Case.model_validate(
        {
            "id": "x-y",
            "title": "Край",
            "source": ".",
            "tags": ["edge"],
            "steps": [{"callback": "order_cancel", "expect": {"silent": True}}],
        }
    )


def test_сырой_callback_без_security_тега_отвергается():
    """Без тега кейс с сырым callback разрешил бы проверку записи молча."""
    with pytest.raises(ValueError, match="callback_data допустим только"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "IDOR без тега",
                "source": ".",
                "steps": [{"callback": "order_cancel", "expect": {"silent": True}}],
            }
        )


def test_сырой_callback_и_клик_вместе_отвергаются():
    with pytest.raises(ValueError, match="ровно один из send, click, pick, callback"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Два действия",
                "source": ".",
                "tags": ["security"],
                "steps": [
                    {
                        "callback": "order_cancel",
                        "click": "Отмена",
                        "expect": {"silent": True},
                    }
                ],
            }
        )


def test_сырой_callback_в_ветке_choice_отвергается():
    """Ветка choice — кнопки, которые бот предлагает; сырой callback — шаг."""
    with pytest.raises(ValueError):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Ветка",
                "source": ".",
                "tags": ["security"],
                "steps": [{"choice": [{"callback": "order_cancel"}], "expect": {"silent": True}}],
            }
        )


def test_подпись_шага_называет_сырой_callback():
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "Подпись",
            "source": ".",
            "tags": ["security"],
            "steps": [{"callback": "order_cancel", "expect": {"silent": True}}],
        }
    )

    assert case.steps[0].label() == "послать сырой callback «order_cancel»"


def _кейс_с_builder(builder: str) -> Case:
    return Case.model_validate(
        {
            "id": "x-y",
            "title": "Адаптер",
            "source": ".",
            "steps": [{"send": "/start", "expect": {"silent": True}}],
            "builder": builder,
        }
    )


@pytest.mark.parametrize("builder", ["legacy", "newgen", "reminder"])
def test_встроенный_builder_принимается(builder):
    assert _кейс_с_builder(builder).builder == builder


@pytest.mark.parametrize(
    "builder",
    [
        "botkit-farm.adapter:mypkg.mine",
        "botkit-farm.adapter:mypkg.adapters.mine:make",
    ],
)
def test_ссылка_на_сторонний_адаптер_принимается(builder):
    """Форма ссылки проверяется на загрузке: опечатку видно до прогона, а
    существование модуля — нет, он в окружении бота."""
    assert _кейс_с_builder(builder).builder == builder


@pytest.mark.parametrize(
    "builder",
    [
        "чепуха",
        "botkit-farm.adapter:",
        "botkit-farm.adapter:mypkg..mine",
        "botkit-farm.adapter:mypkg.mine:бэд",
        "botkit-farm.adapter:mypkg/../mine",
    ],
)
def test_недопустимый_builder_отвергается(builder):
    with pytest.raises(ValueError, match="builder"):
        _кейс_с_builder(builder)


def test_отсутствие_builder_допустимо():
    """builder необязателен: движок определит поколение по коду репозитория."""
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "Без адаптера",
            "source": ".",
            "steps": [{"send": "/start", "expect": {"silent": True}}],
        }
    )
    assert case.builder is None


def test_сырой_режим_по_умолчанию_выключен():
    """Обратная совместимость: старые кейсы ведут себя как раньше."""
    case = _кейс_с_builder("newgen")
    assert case.raw is False


def test_сырой_режим_включается():
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "Сырой режим",
            "source": ".",
            "steps": [{"send": "/start", "expect": {"silent": True}}],
            "raw": True,
        }
    )
    assert case.raw is True


def test_режим_по_умолчанию_polling():
    """Обратная совместимость: без `mode` кейс ведёт себя как раньше."""
    assert _кейс_с_builder("newgen").mode == "polling"


def test_webhook_режим_принимается():
    case = Case.model_validate(
        {
            "id": "x-wh",
            "title": "Webhook",
            "source": ".",
            "steps": [{"send": "/start", "expect": {"silent": True}}],
            "mode": "webhook",
        }
    )
    assert case.mode == "webhook"


def test_неизвестный_режим_отвергается():
    with pytest.raises(ValueError):
        Case.model_validate(
            {
                "id": "x-bad",
                "title": "Плохой режим",
                "source": ".",
                "steps": [{"send": "/start", "expect": {"silent": True}}],
                "mode": "carrier-pigeon",
            }
        )


@pytest.mark.parametrize("bad_id", ["Плохой", "../escape", "with space", "UPPER"])
def test_ид_с_небезопасными_символами_отвергается(bad_id):
    """id попадает в имя файла отчёта и путь кадра — с ../ он бы ушёл из каталога."""
    with pytest.raises(ValueError):
        Case.model_validate(
            {
                "id": bad_id,
                "title": "Плохой id",
                "source": ".",
                "steps": [{"send": "/start", "expect": {"silent": True}}],
            }
        )


# ── загрузчик ───────────────────────────────────────────────────────────────


def test_неизвестная_переменная_шаблона_ловится_до_запуска_бота(tmp_path):
    """С C6 `{{…}}` — это шаблон Jinja2, а не слот. Но неизвестная переменная —
    такой же отказ на чтении: кейс обязан падать до того, как снесено состояние."""
    text = GOOD.replace("Запись", "{{текст_меню}}")
    with pytest.raises(CaseError, match="неизвестные переменные"):
        load_file(_write(tmp_path, text))


def test_слово_заглушка_ловится(tmp_path):
    text = GOOD.replace("Запись", "TODO: взять из кода")
    with pytest.raises(CaseError, match="заглушки"):
        load_file(_write(tmp_path, text))


def _кейс_yaml(steps: str, extra: str = "") -> str:
    return f"""
id: c-1
title: Шаблоны
source: .
{extra}steps:
{steps}
"""


def test_шаблон_с_индексом_повтора_принимается(tmp_path):
    text = _кейс_yaml(
        """  - send: "Товар {{ i }}"
    repeat: 3
    expect:
      text_contains: ["Готово"]
"""
    )
    case = load_file(_write(tmp_path, text))
    assert case.steps[0].repeat == 3


def test_turns_в_действии_отвергается(tmp_path):
    """Действие рендерится до прогона — прошлых ответов ещё не существует."""
    text = _кейс_yaml(
        """  - send: "{{ turns[-1] }}"
    expect:
      text_contains: ["Готово"]
"""
    )
    with pytest.raises(CaseError, match="turns"):
        load_file(_write(tmp_path, text))


def test_turns_в_ожидании_принимается(tmp_path):
    text = _кейс_yaml(
        """  - send: /start
    expect:
      text_contains: ["{{ turns[-1] }}"]
"""
    )
    load_file(_write(tmp_path, text))


def test_неизвестная_переменная_в_ожидании_отвергается(tmp_path):
    text = _кейс_yaml(
        """  - send: /start
    expect:
      text_contains: ["{{ секрет }}"]
"""
    )
    with pytest.raises(CaseError, match="неизвестные переменные"):
        load_file(_write(tmp_path, text))


def test_исполнение_кода_в_шаблоне_отвергается(tmp_path):
    text = _кейс_yaml(
        """  - send: "{{ ''.__class__ }}"
    expect:
      text_contains: ["Готово"]
"""
    )
    with pytest.raises(CaseError, match="приватн"):
        load_file(_write(tmp_path, text))


def test_кейс_на_40_шагов_читается(tmp_path):
    """ТЗ C6 (М): длинный кейс обязан читаться, а не упираться в лимиты.

    Сорок явных шагов — это «поддерживаемый» сценарий: ни модель, ни загрузчик
    не должны ломаться на длине (важно и для сгенерированных кейсов).
    """
    steps = "\n".join(
        f'  - send: "шаг {n}"\n    expect:\n      text_contains: ["ок"]' for n in range(1, 41)
    )
    case = load_file(_write(tmp_path, _кейс_yaml(steps + "\n")))
    assert len(case.steps) == 40
    assert case.steps[0].send == "шаг 1"
    assert case.steps[-1].send == "шаг 40"


def test_битый_yaml_даёт_понятную_ошибку(tmp_path):
    with pytest.raises(CaseError, match="невалидный YAML"):
        load_file(_write(tmp_path, "id: [unclosed\n"))


def test_ошибка_валидации_называет_поле(tmp_path):
    text = GOOD.replace("source: ./bots/botkit-bookingbot", "")
    with pytest.raises(CaseError, match="поле source"):
        load_file(_write(tmp_path, text))


def test_один_битый_файл_не_мешает_остальным(tmp_path):
    (tmp_path / "a.yaml").write_text(GOOD, encoding="utf-8")
    (tmp_path / "b.yaml").write_text("id: битый\n", encoding="utf-8")
    cases, errors = load_dir(tmp_path)
    assert len(cases) == 1
    assert len(errors) == 1


def test_отсутствующий_каталог_не_травит(tmp_path):
    cases, errors = load_dir(tmp_path / "нет-такого")
    assert cases == [] and errors


# ── модель результата ───────────────────────────────────────────────────────


def test_кейс_с_одной_проверкой_и_шагом_без_ожидания_проходит():
    """Не каждый шаг обязан что-то проверять: /start может быть навигационным,
    а проверка — на следующем шаге. Требовать ожидание на каждом шаге было бы
    запретом нормального кейса.
    """
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "Один шаг с проверкой",
            "source": ".",
            "steps": [
                {"send": "/start"},
                {"send": "/help", "expect": {"text_contains": ["Справка"]}},
            ],
        }
    )
    assert len(case.steps) == 2


# ── pick: кнопка по позиции ────────────────────────────────────────────────


def test_pick_принимается_вместо_click():
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "Динамическая дата",
            "source": ".",
            "steps": [{"pick": "first", "expect": {"text_contains": ["Время"]}}],
        }
    )
    assert case.steps[0].pick == "first"
    assert "first" in case.steps[0].label()


@pytest.mark.parametrize("pick", ["first", "last", "0", "3"])
def test_допустимые_значения_pick(pick):
    Case.model_validate(
        {
            "id": "x-y",
            "title": "Pick",
            "source": ".",
            "steps": [{"pick": pick, "expect": {"silent": True}}],
        }
    )


@pytest.mark.parametrize("pick", ["средняя", "-1", "1.5", ""])
def test_мусорный_pick_отвергается(pick):
    with pytest.raises(ValueError):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Pick",
                "source": ".",
                "steps": [{"pick": pick, "expect": {"silent": True}}],
            }
        )


def test_pick_и_click_вместе_отвергаются():
    with pytest.raises(ValueError, match="ровно один"):
        Case.model_validate(
            {
                "id": "x-y",
                "title": "Оба",
                "source": ".",
                "steps": [{"click": "Далее", "pick": "first", "expect": {"silent": True}}],
            }
        )


def test_step_label_обрезается_до_сорока_символов():
    text = "x" * 50
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "Test",
            "source": ".",
            "steps": [{"send": text, "expect": {"silent": True}}],
        }
    )
    assert case.steps[0].label() == f"написать «{'x' * 40}»"


def test_step_label_берёт_первую_строку():
    case = Case.model_validate(
        {
            "id": "x-y",
            "title": "Test",
            "source": ".",
            "steps": [{"send": "первая\nвторая", "expect": {"silent": True}}],
        }
    )
    assert case.steps[0].label() == "написать «первая»"


def test_step_label_для_click_pick():
    case2 = Case.model_validate(
        {
            "id": "x-y2",
            "title": "Test",
            "source": ".",
            "steps": [{"click": "Далее", "expect": {"silent": True}}],
        }
    )
    assert case2.steps[0].label() == "нажать «Далее»"

    case3 = Case.model_validate(
        {
            "id": "x-y3",
            "title": "Test",
            "source": ".",
            "steps": [{"pick": "2", "expect": {"silent": True}}],
        }
    )
    assert case3.steps[0].label() == "нажать кнопку 2"


def test_step_label_использует_ровно_сорок_символов():
    # Проверяем точный подсчёт: внутри кавычек ровно 40 символов
    text = "y" * 41
    case = Case.model_validate(
        {
            "id": "x-yz",
            "title": "Test",
            "source": ".",
            "steps": [{"send": text, "expect": {"silent": True}}],
        }
    )
    lbl = case.steps[0].label()
    # "написать «" + head[:40] + "»"
    assert lbl.startswith("написать «")
    assert lbl.endswith("»")
    inner = lbl[10:-1]  # предположение
    assert len(inner) == 40
