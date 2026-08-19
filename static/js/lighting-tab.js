/**
 * SCCS Lighting tab — sync slider state when the section becomes active.
 */
(function () {
    'use strict';

    const SECTION_ID = 'lighting';

    function isLightingSectionActive() {
        const section = document.getElementById(SECTION_ID);
        return Boolean(
            section &&
            !section.hidden &&
            section.classList.contains('active')
        );
    }

    function refreshLighting() {
        window.SCCS?.lighting?.syncFromServer?.();
    }

    function onSectionChange(sectionId) {
        if (sectionId === SECTION_ID) {
            refreshLighting();
        }
    }

    document.addEventListener('sccs:section-change', (event) => {
        onSectionChange(event.detail?.sectionId);
    });

    if (isLightingSectionActive()) {
        refreshLighting();
    }
})();