(function () {
    const nav = document.querySelector('.nav-pill');
    const stage = document.querySelector('.page-stage');
    if (!nav || !stage) return;

    const items = [...nav.querySelectorAll('.nav-pill__item')];
    const indicator = nav.querySelector('.nav-pill__indicator');
    const sections = [...stage.querySelectorAll('.page-section')];
    const fadeMs = parseFloat(getComputedStyle(document.documentElement)
        .getPropertyValue('--duration-page-fade')) * 1000 || 450;

    let switching = false;

    function moveIndicator(item) {
        if (!indicator || !item) return;
        const navRect = nav.getBoundingClientRect();
        const itemRect = item.getBoundingClientRect();
        indicator.style.width = `${itemRect.width}px`;
        indicator.style.left = `${itemRect.left - navRect.left}px`;
        indicator.style.transform = '';
    }

    function waitForFade() {
        return new Promise((resolve) => setTimeout(resolve, fadeMs));
    }

    function setNavActive(sectionId) {
        items.forEach((item) => {
            const isActive = item.dataset.section === sectionId;
            item.classList.toggle('active', isActive);
            item.setAttribute('aria-selected', String(isActive));
        });
        const activeItem = items.find((item) => item.classList.contains('active'));
        moveIndicator(activeItem);
    }

    async function activate(sectionId) {
        if (switching) return;

        const current = sections.find((section) => section.classList.contains('active'));
        const next = document.getElementById(sectionId);
        if (!next || current === next) return;

        switching = true;
        setNavActive(sectionId);

        next.hidden = false;
        void next.offsetWidth;

        if (current) {
            current.classList.add('is-leaving');
        }
        next.classList.add('active');
        if (current) {
            current.classList.remove('active');
        }

        await waitForFade();

        if (current) {
            current.classList.remove('is-leaving');
            current.hidden = true;
        }

        document.dispatchEvent(new CustomEvent('sccs:section-change', {
            detail: { sectionId },
        }));

        switching = false;
    }

    items.forEach((item) => {
        item.addEventListener('click', () => activate(item.dataset.section));
    });

    window.addEventListener('resize', () => {
        const activeItem = items.find((item) => item.classList.contains('active'));
        moveIndicator(activeItem);
    });

    const initial = items.find((item) => item.classList.contains('active')) || items[0];
    setNavActive(initial.dataset.section);
})();