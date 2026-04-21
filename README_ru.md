# QwenPaw Плагин мультиользователя

> Производная работа от [QwenPaw](https://github.com/agentscope-ai/QwenPaw) (Apache License 2.0). Превратите однопользовательский QwenPaw в мультиользовательскую платформу с **одной переменной окружения**.

---

**License**: [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)  
**Базовый проект**: [QwenPaw](https://github.com/agentscope-ai/QwenPaw) | Apache License 2.0  
**Copyright**: Copyright 2024 QwenPawTeam Authors

---

## Возможности

| Возможность | Описание |
|-------------|----------|
| 🔐 **Встроенная авторизация** | HMAC-SHA256 вход, поддержка SSO/OAuth через пользовательский TokenParser |
| 📁 **Полная изоляция данных** | Рабочие пространства, конфигурация, API-ключи, переменные окружения, статистика токенов, логи и резервные копии — всё изолировано по пользователям |
| 🧩 **Гибкие поля пользователя** | Одно поле (username) = простая мультиользователь; несколько полей (orgId/deptId/userId) = иерархическая изоляция на уровне предприятия |
| 🌍 **Интерфейс на 4 языках** | Форма входа и информация о пользователе поддерживают 中文 / English / 日本語 / Русский |
| 🔄 **Авторегистрация** | Предварительная настройка администратора через переменные окружения — готово к работе, идеально для Docker / K8s |
| 🎯 **Нулевые изменения в основном коде** | Только 2 изменения в файлах основного проекта (общие хуки плагинов), легко обновлять |
| ✅ **Полная обратная совместимость** | Отключение плагина = оригинальный однопользовательский QwenPaw |

---

## Быстрый старт

### 1. Установите переменные окружения

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_AUTH_USERNAME=admin
QWENPAW_AUTH_PASSWORD=changeme
```

### 2. Запустите сервер

```bash
python run_server.py
```

### 3. Откройте браузер

Перейдите на `http://localhost:8000/login` и войдите с именем пользователя и паролем, которые вы настроили выше.

---

## Типичные сценарии

### Сценарий A: Простая мультиользователь (разработка / небольшая команда)

Все пользователи используют общую конфигурацию, разделение только по имени пользователя.

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_AUTH_USERNAME=admin
QWENPAW_AUTH_PASSWORD=secret123
```

Каждый пользователь получает независимое рабочее пространство: `WORKING_DIR/users/{username}/`

> **Без регистрации**: новые пользователи могут просто войти с новым именем пользователя + паролем, и аккаунт будет создан автоматически.

### Сценарий B: Иерархическая изоляция на уровне предприятия (несколько полей)

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=true
QWENPAW_USER_FIELDS=orgId,deptId,userId

# Китайские метки
QWENPAW_USER_FIELD_LABELS_ZH={"orgId":"机构编号","deptId":"部门编号","userId":"用户编号"}
# Английские метки
QWENPAW_USER_FIELD_LABELS_EN={"orgId":"Organization ID","deptId":"Department ID","userId":"User ID"}
# Японские метки
QWENPAW_USER_FIELD_LABELS_JA={"orgId":"組織ID","deptId":"部門ID","userId":"ユーザーID"}
# Русские метки
QWENPAW_USER_FIELD_LABELS_RU={"orgId":"ИД организации","deptId":"ИД отдела","userId":"ИД пользователя"}

# Аккаунт администратора
QWENPAW_AUTH_ORGID=ACME
QWENPAW_AUTH_DEPTID=ENG
QWENPAW_AUTH_USERID=alice
QWENPAW_AUTH_PASSWORD=secret123
```

Структура каталогов: `WORKING_DIR/users/ACME/ENG/alice/`

Форма входа динамически отображается на основе `QWENPAW_USER_FIELDS` и автоматически переключает язык в соответствии с настройками браузера.

### Сценарий C: Интеграция с SSO / Шлюзом

Подключение к существующим системам, таким как Keycloak, Auth0 или прокси-сервер OAuth Nginx.

```bash
QWENPAW_MULTI_USER_ENABLED=true
QWENPAW_AUTH_ENABLED=false
QWENPAW_USER_FIELDS=orgId,userId
QWENPAW_TOKEN_PARSER_MODULE=my_sso.parser
```

Реализуйте пользовательский TokenParser:

```python
from qwenpaw_plugins.multi_user.token_parser import TokenParser

class KeycloakTokenParser(TokenParser):
    def parse(self, token: str):
        import jwt
        payload = jwt.decode(token, options={"verify_signature": False})
        return {
            "orgId": payload.get("org_id", ""),
            "userId": payload.get("sub", ""),
        }

def create_token_parser() -> TokenParser:
    return KeycloakTokenParser()
```

---

## Справочник конфигурации

| Переменная окружения | Описание | По умолчанию |
|---------------------|----------|--------------|
| `QWENPAW_MULTI_USER_ENABLED` | Включить плагин мультиользователя | `true` |
| `QWENPAW_AUTH_ENABLED` | Включить встроенную HMAC-авторизацию | `true` |
| `QWENPAW_USER_FIELDS` | Имена полей пользователя, через запятую | `username` |
| `QWENPAW_USER_FIELD_LABELS_ZH` | Китайские метки | `{"username":"用户名"}` |
| `QWENPAW_USER_FIELD_LABELS_EN` | Английские метки | `{"username":"Username"}` |
| `QWENPAW_USER_FIELD_LABELS_JA` | Японские метки | `{"username":"ユーザー名"}` |
| `QWENPAW_USER_FIELD_LABELS_RU` | Русские метки | `{"username":"Имя пользователя"}` |
| `QWENPAW_TOKEN_PARSER_MODULE` | Путь к модулю пользовательского TokenParser | Встроенный парсер |

---

## Изоляция данных

| Категория данных | Изоляция |
|-----------------|----------|
| Рабочее пространство (агенты, диалоги, память, навыки) | Независимый каталог на каждого пользователя |
| Файл конфигурации (config.json) | По одному на пользователя |
| API-ключи / Учётные данные провайдера | Независимые переопределения на каждого пользователя |
| Переменные окружения (envs.json) | Независимо на каждого пользователя |
| Статистика использования токенов | На каждый (пользователь × агент) |
| Бэкенд-логи | Независимый лог-файл на каждого пользователя |
| Резервное копирование / Восстановление | Независимый каталог резервных копий на каждого пользователя |

---

## Поддерживаемые языки

Интерфейс входа поддерживает **4 языка** и автоматически переключается в зависимости от языковых настроек браузера:

| Язык | Код языка | Статус |
|------|-----------|--------|
| 🇨🇳 中文 | `zh` | ✅ Полная поддержка |
| 🇺🇸 English | `en` | ✅ Полная поддержка |
| 🇯🇵 日本語 | `ja` | ✅ Полная поддержка |
| 🇷🇺 Русский | `ru` | ✅ Полная поддержка |

---

## API эндпоинты

| Эндпоинт | Метод | Описание |
|----------|-------|----------|
| `/api/auth/login` | POST | Вход с динамическими полями; авторегистрация новых пользователей |
| `/api/auth/status` | GET | Возвращает статус авторизации, поля пользователя и метки интерфейса |
| `/api/auth/verify` | GET | Проверить валидность Bearer токена |
| `/api/auth/resolve-user` | GET | Извлечь идентификатор пользователя из upstream токена |
| `/api/auth/init-workspace` | POST | Инициализировать рабочее пространство пользователя (только в режиме интеграции) |
| `/api/auth/users` | GET | Список всех зарегистрированных пользователей |
| `/api/auth/update-profile` | POST | Изменить пароль |
| `/api/auth/users/{id}` | DELETE | Удалить аккаунт пользователя |

---

## Часто задаваемые вопросы

**Q: Потеряются ли данные при отключении плагина?**  
A: Нет. При отключении система возвращается в однопользовательский режим. Каталог `users/` игнорируется, все исходные данные остаются нетронутыми.

**Q: Можно ли добавить поля пользователя после начальной настройки?**  
A: Да. Просто обновите `QWENPAW_USER_FIELDS` и соответствующие переменные меток, затем перезапустите.

**Q: Нужна ли внешняя база данных?**  
A: Нет. Данные пользователей хранятся в `SECRET_DIR/auth.json` (JSON-файл).

**Q: Могут ли разные пользователи использовать разные API-ключи LLM?**  
A: Да. Каждый пользователь может настроить свои собственные API-ключи для переопределения глобальных настроек.

---

## Структура файлов плагина

```
src/qwenpaw_plugins/multi_user/
├── __init__.py              # Входная точка плагина (activate/deactivate)
├── constants.py              # Имена переменных окружения, метки по умолчанию
├── user_context.py           # Асинхронное распространение ID пользователя (ContextVar)
├── token_parser.py           # Подключаемый TokenParser
├── auth_extension.py         # HMAC авторизация, AuthMiddleware
├── router_extension.py       # 8 эндпоинтов auth API
├── manager_extension.py      # Обёртка UserAware MultiAgentManager
├── provider_extension.py     # Overlay учётных данных UserAware ProviderManager
├── config_extension.py       # Конфигурация на каждого пользователя (monkey-patch)
├── envs_extension.py         # Переменные окружения на каждого пользователя (monkey-patch)
├── agents_extension.py       # Каталог рабочего пространства на каждого пользователя
├── migration_extension.py    # Ленивая инициализация рабочего пространства
├── token_usage_extension.py  # Статистика токенов на каждого пользователя
├── console_extension.py      # Бэкенд-логи на каждого пользователя
├── backup_extension.py       # Резервное копирование / восстановление на каждого пользователя
└── middleware.py             # Фабрика middleware
```
