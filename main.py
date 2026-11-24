# трекер доходов и расходов на kivy, весь код в одном файле
# 1. графики (matplotlib + kivy garden) - кнопка "Графики"
# 2. облако (firebase) - кнопки "В облако" и "Из облака"
# 3. категории доходов и баланс - кнопки "+ Доход" и "+ Расход"
# установка: pip install kivy matplotlib kivy_garden.matplotlib
# запуск: python main.py (не в jupyter)
# для облака в firebase в Rules нужно поставить true для read и write
import csv
import json
import math
import os
import ssl
import threading
import urllib.error
import urllib.request
from datetime import date, datetime
from io import BytesIO

from kivy.app import App
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.properties import NumericProperty, StringProperty
from kivy.storage.jsonstore import JsonStore
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.popup import Popup
from kivy.uix.recycleboxlayout import RecycleBoxLayout  # нужен для kv
from kivy.uix.recycleview import RecycleView  # нужен для kv
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.utils import platform
from matplotlib.figure import Figure

# настройки
FIREBASE_URL = "https://expense-tracker-f4c11-default-rtdb.firebaseio.com"  # адрес моей базы

CATEGORIES = {
    "Расход": ["Аренда", "Зарплата", "Маркетинг", "Закупки", "Транспорт", "Налоги", "Прочее"],
    "Доход": ["Продажи", "Услуги", "Инвестиции", "Возвраты", "Прочее"],
}

# холст из kivy garden, если не получится то график будет картинкой
try:
    from kivy_garden.matplotlib.backend_kivyagg import FigureCanvasKivyAgg
except Exception:
    FigureCanvasKivyAgg = None


try:
    import certifi   # сертификаты для https, на маке без них бывает ошибка
except ImportError:
    certifi = None


# вспомогательные функции
def firebase_request(method, url, data=None):
    # запрос к firebase, возвращает ответ и текст ошибки
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    request = urllib.request.Request(url, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
    context = ssl.create_default_context(cafile=certifi.where() if certifi else None)
    try:
        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            text = response.read().decode("utf-8")
        return (json.loads(text) if text.strip() else None), None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return None, "Доступ запрещён: в Firebase откройте Rules, поставьте true и нажмите Publish"
        if e.code == 404:
            return None, "База не найдена: проверьте FIREBASE_URL"
        return None, f"Ошибка облака, код {e.code}"
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            return None, "Ошибка SSL-сертификата: выполните pip install --upgrade certifi"
        return None, "Нет соединения с интернетом или неверный адрес базы"
    except (OSError, ValueError):
        return None, "Облако не ответило или вернуло не JSON"


def money(x):
    # красивая сумма, 1500.5 -> 1 500,50
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def check(r):
    # проверка записи, нужна для данных из облака
    try:
        amount = r["amount"]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            return None
        if not math.isfinite(amount) or not 0 < amount < 1e12 or r["type"] not in CATEGORIES:
            return None
        return {
            "type": r["type"],
            "category": str(r["category"])[:50],
            "description": str(r.get("description", ""))[:200],
            "amount": round(float(amount), 2),
            "date": date.fromisoformat(str(r["date"])).isoformat(),
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def csv_safe(text):
    # чтобы excel не принял текст за формулу
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def to_widget(fig):
    # превращаем график в виджет
    if FigureCanvasKivyAgg is not None:
        try:
            return FigureCanvasKivyAgg(fig)
        except Exception:
            pass
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    buf.seek(0)
    return Image(texture=CoreImage(buf, ext="png").texture, fit_mode="contain")


def pie_figure(by_category, title):
    fig = Figure(figsize=(4, 3.2), layout="tight")
    ax = fig.add_subplot(111)
    if by_category:
        ax.pie(list(by_category.values()), labels=list(by_category), autopct="%1.0f%%",
               startangle=90, textprops={"fontsize": 8})
        ax.set_title(f"{title}: {money(sum(by_category.values()))} руб.", fontsize=11)
    else:
        ax.text(0.5, 0.5, "Нет данных", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title, fontsize=11)
        ax.axis("off")
    return fig


def month_figure(by_month):
    # by_month = {'2026-10': [доходы, расходы]}
    fig = Figure(figsize=(4, 3.2), layout="tight")
    ax = fig.add_subplot(111)
    ax.set_title("Доходы и расходы по месяцам", fontsize=11)
    if by_month:
        months = sorted(by_month)[-6:]
        x = list(range(len(months)))
        ax.bar([i - 0.2 for i in x], [by_month[m][0] for m in months], 0.4, label="Доходы", color="#2e9e5b")
        ax.bar([i + 0.2 for i in x], [by_month[m][1] for m in months], 0.4, label="Расходы", color="#d64545")
        ax.set_xticks(x)
        ax.set_xticklabels(months, fontsize=8)
        ax.legend(fontsize=8)
    else:
        ax.text(0.5, 0.5, "Нет данных", ha="center", va="center", transform=ax.transAxes)
        ax.axis("off")
    return fig


# интерфейс на kv
Builder.load_string(r'''
<Row>:
    size_hint_y: None
    height: dp(60)
    padding: dp(10), dp(4)
    canvas.before:
        Color:
            rgba: (0.12, 0.27, 0.18, 1) if root.kind == 'Доход' else (0.30, 0.14, 0.14, 1)
        Rectangle:
            pos: self.pos
            size: self.size
    Label:
        text: root.title
        halign: 'left'
        valign: 'middle'
        text_size: self.size
        max_lines: 2
    Label:
        text: root.amount_text
        bold: True
        size_hint_x: None
        width: dp(130)
        halign: 'right'
        valign: 'middle'
        text_size: self.size
        color: (0.37, 0.83, 0.55, 1) if root.kind == 'Доход' else (1, 0.45, 0.45, 1)
    Button:
        text: 'x'
        size_hint_x: None
        width: dp(40)
        on_release: app.delete(root.index)

<ItemList@RecycleView>:
    viewclass: 'Row'
    RecycleBoxLayout:
        default_size: None, dp(60)
        default_size_hint: 1, None
        size_hint_y: None
        height: self.minimum_height
        orientation: 'vertical'
        spacing: dp(3)

<MainScreen>:
    name: 'main'
    BoxLayout:
        orientation: 'vertical'
        padding: dp(12)
        spacing: dp(8)
        Label:
            text: 'ExpenseTracker'
            font_size: sp(22)
            bold: True
            size_hint_y: None
            height: dp(36)
        Label:
            markup: True
            halign: 'center'
            size_hint_y: None
            height: dp(44)
            text: 'Доходы: [color=#5fd38d]' + root.income + '[/color]    Расходы: [color=#ff7373]' + root.expense + '[/color]'
        Label:
            markup: True
            font_size: sp(20)
            size_hint_y: None
            height: dp(34)
            text: 'Баланс: [b]' + root.balance + ' руб.[/b]'
        GridLayout:
            cols: 3
            size_hint_y: None
            height: dp(100)
            spacing: dp(6)
            Button:
                text: '+ Доход'
                on_release: app.add('Доход')
            Button:
                text: '+ Расход'
                on_release: app.add('Расход')
            Button:
                text: 'Графики'
                on_release: app.show_charts()
            Button:
                text: 'В облако'
                on_release: app.upload()
            Button:
                text: 'Из облака'
                on_release: app.download()
            Button:
                text: 'Экспорт CSV'
                on_release: app.export_csv()
        ItemList:
            id: rv
        Label:
            text: app.status
            font_size: sp(13)
            size_hint_y: None
            height: dp(40)
            text_size: self.width, None
            halign: 'center'

<ChartScreen>:
    name: 'charts'
    BoxLayout:
        orientation: 'vertical'
        padding: dp(10)
        spacing: dp(6)
        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(8)
            Button:
                text: '< Назад'
                size_hint_x: None
                width: dp(110)
                on_release: app.show_main()
            Spinner:
                id: kind
                text: 'Расход'
                values: ('Расход', 'Доход')
                on_text: root.draw()
        BoxLayout:
            id: pie
        BoxLayout:
            id: bars

<AddPopup>:
    size_hint: 0.92, None
    height: dp(400)
    auto_dismiss: False
    BoxLayout:
        orientation: 'vertical'
        spacing: dp(6)
        Spinner:
            id: category
            size_hint_y: None
            height: dp(42)
        TextInput:
            id: amount
            hint_text: 'Сумма, руб. (например 1500,50)'
            multiline: False
            size_hint_y: None
            height: dp(42)
        TextInput:
            id: description
            hint_text: 'Описание (необязательно)'
            multiline: False
            size_hint_y: None
            height: dp(42)
        TextInput:
            id: day
            hint_text: 'Дата ГГГГ-ММ-ДД'
            multiline: False
            size_hint_y: None
            height: dp(42)
        Label:
            id: error
            color: 1, 0.45, 0.45, 1
        BoxLayout:
            size_hint_y: None
            height: dp(46)
            spacing: dp(8)
            Button:
                text: 'Отмена'
                on_release: root.dismiss()
            Button:
                text: 'Сохранить'
                on_release: root.save()
''')


# экраны и окна
class Row(BoxLayout):
    # одна строка списка
    index = NumericProperty(0)
    kind = StringProperty("Расход")
    title = StringProperty("")
    amount_text = StringProperty("")


class MainScreen(Screen):
    income = StringProperty("0,00")
    expense = StringProperty("0,00")
    balance = StringProperty("0,00")

    def on_pre_enter(self, *args):
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        income = sum(i["amount"] for i in app.items if i["type"] == "Доход")
        expense = sum(i["amount"] for i in app.items if i["type"] == "Расход")
        self.income, self.expense = money(income), money(expense)
        self.balance = money(income - expense)   # баланс = доходы - расходы
        rows = []
        for index, i in reversed(list(enumerate(app.items))):  # новые записи сверху
            sign = "+" if i["type"] == "Доход" else "-"
            rows.append({
                "index": index,
                "kind": i["type"],
                "title": f"{i['category']}  {i['date']}" + (f"\n{i['description']}" if i["description"] else ""),
                "amount_text": f"{sign}{money(i['amount'])} руб.",
            })
        self.ids.rv.data = rows


class ChartScreen(Screen):
    def on_pre_enter(self, *args):
        self.draw()

    def draw(self):
        if "bars" not in self.ids:   # экран еще не создан
            return
        items = App.get_running_app().items
        kind = self.ids.kind.text
        by_category, by_month = {}, {}
        for i in items:
            if i["type"] == kind:
                by_category[i["category"]] = by_category.get(i["category"], 0) + i["amount"]
            m = by_month.setdefault(i["date"][:7], [0, 0])
            m[0 if i["type"] == "Доход" else 1] += i["amount"]
        for box, fig in ((self.ids.pie, pie_figure(by_category, kind + "ы")),
                         (self.ids.bars, month_figure(by_month))):
            box.clear_widgets()
            box.add_widget(to_widget(fig))


class AddPopup(Popup):
    # окно добавления дохода или расхода

    def __init__(self, kind, **kwargs):
        super().__init__(**kwargs)
        self.kind = kind
        self.title = "Новый " + ("доход" if kind == "Доход" else "расход")
        self.ids.category.values = CATEGORIES[kind]
        self.ids.category.text = CATEGORIES[kind][0]
        self.ids.day.text = date.today().isoformat()

    def save(self):
        try:
            amount = float(self.ids.amount.text.replace(" ", "").replace(",", "."))
            if not 0 < amount < 1e12:   # nan и inf тоже не проходят
                raise ValueError
        except ValueError:
            self.ids.error.text = "Сумма должна быть числом больше 0"
            return
        try:
            day = date.fromisoformat(self.ids.day.text.strip()).isoformat()
        except ValueError:
            self.ids.error.text = "Дата в формате ГГГГ-ММ-ДД"
            return
        App.get_running_app().save_item({
            "type": self.kind,
            "category": self.ids.category.text,
            "description": " ".join(self.ids.description.text.split())[:200],
            "amount": round(amount, 2),
            "date": day,
        })
        self.dismiss()


# приложение
class ExpenseApp(App):
    status = StringProperty("")

    def build(self):
        Window.clearcolor = (0.09, 0.10, 0.12, 1)
        self.store = JsonStore(os.path.join(self.user_data_dir, "expenses.json"))
        saved = self.store.get("data")["items"] if self.store.exists("data") else []
        self.items = [r for r in map(check, saved) if r]
        self.sm = ScreenManager()
        self.sm.add_widget(MainScreen())
        self.sm.add_widget(ChartScreen())
        return self.sm

    # работа с данными
    def save_to_disk(self):
        self.store.put("data", items=self.items)

    def add(self, kind):
        AddPopup(kind).open()

    def save_item(self, item):
        self.items.append(item)
        self.save_to_disk()
        self.sm.get_screen("main").refresh()
        self.status = "Запись добавлена"

    def delete(self, index):
        if 0 <= index < len(self.items):
            del self.items[index]
            self.save_to_disk()
            self.sm.get_screen("main").refresh()
            self.status = "Запись удалена"

    # переключение экранов
    def show_charts(self):
        self.sm.current = "charts"

    def show_main(self):
        self.sm.current = "main"

    # облако (firebase)
    def cloud(self, method, data, on_done, text):
        # запрос в отдельном потоке, чтобы окно не зависало
        if not FIREBASE_URL:
            self.status = "Впишите FIREBASE_URL в начале main.py"
            return
        url = FIREBASE_URL.strip().rstrip("/") + "/expenses.json"
        self.status = text

        def work():
            result, error = firebase_request(method, url, data)
            Clock.schedule_once(lambda dt: on_done(result, error))

        threading.Thread(target=work, daemon=True).start()

    def upload(self):
        # отправляем все записи в облако, старые данные в облаке заменятся
        def done(result, error):
            self.status = error or f"В облако отправлено записей: {len(self.items)}"
        self.cloud("PUT", self.items, done, "Отправка...")

    def download(self):
        # загружаем записи из облака, локальные заменятся
        self.cloud("GET", None, self.downloaded, "Загрузка...")

    def downloaded(self, result, error):
        if error:
            self.status = error
            return
        if not result:
            self.status = "В облаке пока пусто — сначала нажмите «В облако»"
            return
        if isinstance(result, dict):
            result = list(result.values())
        if not isinstance(result, list):
            self.status = "Облако вернуло данные неверного формата"
            return
        self.items = [r for r in map(check, result) if r]   # проверяем данные из облака
        self.save_to_disk()
        self.sm.get_screen("main").refresh()
        self.status = f"Из облака загружено записей: {len(self.items)}"

    # экспорт в csv
    def export_csv(self):
        folder = os.path.join(os.path.expanduser("~"), "Documents")
        if platform not in ("win", "linux", "macosx") or not os.path.isdir(folder):
            folder = self.user_data_dir
        path = os.path.join(folder, datetime.now().strftime("expenses_%Y%m%d_%H%M%S.csv"))
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Дата", "Тип", "Категория", "Описание", "Сумма"])
                for i in self.items:
                    w.writerow([i["date"], i["type"], csv_safe(i["category"]),
                                csv_safe(i["description"]), f"{i['amount']:.2f}".replace(".", ",")])
            self.status = "Файл сохранён: " + path
        except OSError:
            self.status = "Не удалось сохранить файл"


if __name__ == "__main__":
    if platform in ("win", "linux", "macosx"):
        Window.size = (440, 780)
    ExpenseApp().run()
