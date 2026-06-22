/*
 * Подписание ЭЦП на портале для медицинских работников.
 *
 * ВАЖНО: НЕ помечать как @odoo-module — иначе попадает в web.assets_frontend_lazy
 * и не выполняется пока кто-то не импортирует. На портале использовать как
 * обычный legacy script — IIFE, выполняется сразу при загрузке bundle.
 *
 * Архитектурно идентично backend-флоу (sign_pdf_esp.js / NCALayerSignButton):
 *   1. GET документ для подписи  → /esp/get_sign_obj/<id>
 *   2. CMS-подпись через NCALayer
 *   3. Верификация и регистрация → /sign_esp  (с partnerId — заставляет бэкенд
 *      использовать user.partner_id.vat для проверки ИИН, у портального
 *      пользователя нет привязки к hr.employee)
 *   4. Сохранение CMS            → /esp/save_signed_pdf/<id>
 *   5. Редирект на success-страницу
 *
 * NCANode нигде не нужен — вся верификация локальная через cryptography
 * (на стороне колежиего appstream_approval).
 */

(function () {
    "use strict";

    // Если DOM уже распарсен (модуль грузится после DOMContentLoaded) —
    // привязываемся сразу, иначе ждём ивент.
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", attachHandler);
    } else {
        attachHandler();
    }

    function attachHandler() {
        const button = document.querySelector(".o_corr_portal_sign_btn");
        if (!button) {
            return;
        }
        button.addEventListener("click", onSignClick);
    }

    /**
     * Wrapper над fetch для JSON-RPC эндпоинтов Odoo.
     * Распаковывает {result: ...} / {error: ...}, бросает Error при ошибке транспорта.
     */
    async function jsonRpc(url, params) {
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                jsonrpc: "2.0",
                method: "call",
                params: params || {},
            }),
        });
        if (!response.ok) {
            throw new Error("HTTP " + response.status + " от " + url);
        }
        const data = await response.json();
        if (data.error) {
            const msg = (data.error.data && data.error.data.message) || data.error.message || "RPC error";
            throw new Error(msg);
        }
        return data.result;
    }

    async function onSignClick(event) {
        const button = event.currentTarget;
        const documentId = parseInt(button.dataset.documentId, 10);
        const partnerId = parseInt(button.dataset.partnerId, 10);
        const resModel = "corr.outgoing";

        if (!documentId || !partnerId) {
            alert("Не удалось определить документ или партнёра. Перезагрузите страницу.");
            return;
        }

        const originalHTML = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Подписание...';

        try {
            // ---- 1. Получаем документ для подписи ---------------------------
            const signObj = await jsonRpc("/esp/get_sign_obj/" + documentId, {
                resModel: resModel,
            });
            if (signObj.error) {
                alert("Ошибка получения документа: " + signObj.error);
                return;
            }
            if (!signObj.obj_to_sign) {
                alert("Документ для подписания не найден");
                return;
            }
            const espType = signObj.esp_type || "cms";
            const objToSign = signObj.obj_to_sign;

            // ---- 2. Подключаемся к NCALayer ---------------------------------
            if (typeof window.NCALayerClient !== "function") {
                alert("NCALayer client не загружен. Перезагрузите страницу (Ctrl+Shift+R).");
                return;
            }

            const client = new window.NCALayerClient();
            try {
                await client.connect();
            } catch (e) {
                alert(
                    "Не удалось подключиться к NCALayer. Запустите NCALayer и попробуйте снова.\n\n" + e
                );
                return;
            }

            // ---- 3. Подписываем (CMS, точно те же параметры что и backend) --
            let signedData;
            try {
                signedData = await client.basicsSign(
                    ["PKCS12"],
                    espType,
                    objToSign,
                    {
                        decode: true,
                        encapsulate: true,
                        digested: false,
                        tsaProfile: {},
                    },
                    {
                        extKeyUsageOids: ["1.3.6.1.5.5.7.3.4"],
                    },
                    "ru"
                );
            } catch (e) {
                alert("Подписание отменено или произошла ошибка:\n" + e);
                return;
            }

            // basicsSign возвращает либо string, либо [string]
            const isValid =
                signedData &&
                ((typeof signedData === "string" && signedData.trim() !== "") ||
                    (Array.isArray(signedData) &&
                        signedData.length > 0 &&
                        typeof signedData[0] === "string" &&
                        signedData[0].trim() !== ""));
            if (!isValid) {
                alert("Пустая подпись, попробуйте ещё раз.");
                return;
            }

            // ---- 4. Отправляем на бэкенд для верификации и регистрации ------
            const signResult = await jsonRpc("/sign_esp", {
                signed_data: signedData,
                esp_type: espType,
                resModel: resModel,
                resId: documentId,
                partnerId: partnerId,
            });

            if (signResult.status !== 200) {
                alert(signResult.message || "Ошибка при сохранении подписи");
                return;
            }

            // ---- 5. Сохраняем подписанный файл в appstream.approval.esp -----
            await jsonRpc("/esp/save_signed_pdf/" + documentId, {
                signedCMS: signedData,
                espType: espType,
                resModel: resModel,
            });

            // ---- 6. Редиректим на success-страницу --------------------------
            window.location.href = "/correspondence/signed/success";
        } catch (err) {
            console.error("Portal sign error:", err);
            alert("Ошибка: " + (err.message || err));
        } finally {
            button.disabled = false;
            button.innerHTML = originalHTML;
        }
    }
})();