# Защита cookie-сессий от CSRF

После входа через Яндекс ID браузер хранит непрозрачную сессионную cookie. Браузер
может автоматически приложить её к запросу, отправленному с чужого сайта. Поэтому
CORS недостаточен: он запрещает чужому JavaScript прочитать ответ, но сам POST-запрос
может быть отправлен.

Для небезопасных запросов (`POST`, `PUT`, `PATCH`, `DELETE`) к `/api/`, которые несут
cookie сессии, backend требует оба условия:

- `Origin` точно равен origin из `YANDEX_REDIRECT_URI`;
- если браузер передал `Sec-Fetch-Site`, его значение равно `same-origin`.

При нарушении сервер отвечает `403` с `{"detail":"Cross-site request rejected."}` и
не выполняет обработчик endpoint. Проверка не применяется к безопасным методам и к
запросам без cookie, поэтому не заменяет обычную проверку аутентификации.

`YANDEX_REDIRECT_URI` допускает только точный callback-path
`/api/v1/auth/yandex/callback`. В production используйте HTTPS. Обычный HTTP разрешён
лишь для `localhost` или loopback-IP при `YANDEX_SESSION_COOKIE_SECURE=false` — только
для локальной разработки.

Основание подхода: [OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
и описание [Sec-Fetch-Site в MDN](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Sec-Fetch-Site).
