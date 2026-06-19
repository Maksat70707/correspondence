/** @odoo-module **/

document.addEventListener('DOMContentLoaded', () => {
    const button = document.querySelector('.o_corr_portal_sign_btn');
    if (!button) return;
    button.addEventListener('click', signCorrespondenceEsp);
});

async function signCorrespondenceEsp(event) {
    const button = event.currentTarget;
    const documentId = button.dataset.documentId;
    const partnerId = button.dataset.partnerId;
    const name = button.dataset.documentName;

    // Блокируем кнопку чтобы не клацали дважды
    button.disabled = true;
    const origText = button.innerHTML;
    button.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Подписание...';

    const xml = `<data><model>corr.outgoing</model><id>${documentId}</id>` +
                `<uid>${partnerId}</uid><display_name>${name}</display_name>` +
                `<n>${name}</n></data>`;

    try {
        if (typeof window.NCALayerClient !== 'function') {
            alert("NCALayer client не загружен. Перезагрузите страницу.");
            return;
        }

        const client = new window.NCALayerClient();
        try {
            await client.connect();
        } catch (e) {
            alert("Не удалось подключиться к NCALayer. Запустите NCALayer и попробуйте снова.\n\n" + e);
            return;
        }

        let signedXml;
        try {
            signedXml = await client.basicsSignXML(
                window.NCALayerClient.basicsStoragesAll,
                xml,
                window.NCALayerClient.basicsXMLParams,
                window.NCALayerClient.basicsSignerSignAny,
                'ru',
            );
        } catch (e) {
            alert("Подписание отменено или произошла ошибка:\n" + e);
            return;
        }

        // Отправляем подписанный XML на сервер
        const response = await fetch('/correspondence/sign/esp/success', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                jsonrpc: '2.0',
                method: 'call',
                params: {
                    document_id: documentId,
                    uid: partnerId,
                    name: name,
                    xml: signedXml,
                },
            }),
        });

        const data = await response.json();
        const result = data.result || {};
        if (result.status === 200) {
            window.location.href = result.redirect_url || '/correspondence/signed/success';
        } else {
            alert(result.message || "Не удалось сохранить подпись");
        }
    } catch (e) {
        alert("Ошибка: " + e);
    } finally {
        button.disabled = false;
        button.innerHTML = origText;
    }
}