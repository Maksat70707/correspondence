import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

/**
 * «Предпросмотр» письма: PDF в окне поверх формы, тем же просмотрщиком
 * pdf.js, что окно подписи appstream_approval. Не браузерным: Chrome на
 * Android PDF внутри страницы не показывает, а браузер с настройкой
 * «скачивать PDF» вместо показа скачал бы файл.
 */
export class CorrPreviewAction extends Component {
    static template = "correspondence.PreviewAction";
    static props = { ...standardActionServiceProps };

    get viewerUrl() {
        const url = this.props.action.params.url;
        return `/web/static/lib/pdfjs/web/viewer.html?file=${encodeURIComponent(url)}#pagemode=none`;
    }
}

registry.category("actions").add("correspondence.preview", CorrPreviewAction);
