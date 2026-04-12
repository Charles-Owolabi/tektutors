document.addEventListener("DOMContentLoaded", () => {
    const menuToggle = document.getElementById("menuToggle");
    const mainMenu = document.getElementById("mainMenu");

    if (menuToggle && mainMenu) {
        menuToggle.addEventListener("click", () => {
            const isOpen = mainMenu.classList.toggle("is-open");
            menuToggle.setAttribute("aria-expanded", String(isOpen));
        });

        mainMenu.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", () => {
                mainMenu.classList.remove("is-open");
                menuToggle.setAttribute("aria-expanded", "false");
            });
        });
    }

    const revealElements = document.querySelectorAll(".reveal");
    if (revealElements.length > 0) {
        const revealObserver = new IntersectionObserver(
            (entries, observer) => {
                entries.forEach((entry) => {
                    if (!entry.isIntersecting) {
                        return;
                    }
                    entry.target.classList.add("visible");
                    observer.unobserve(entry.target);
                });
            },
            {
                threshold: 0.12,
                rootMargin: "0px 0px -40px 0px",
            }
        );

        revealElements.forEach((element) => revealObserver.observe(element));
    }

    const parallaxElements = Array.from(document.querySelectorAll("[data-parallax]"));
    if (parallaxElements.length > 0) {
        const updateParallax = () => {
            const offset = window.scrollY;
            parallaxElements.forEach((element) => {
                const speed = Number(element.getAttribute("data-speed") || 0.12);
                element.style.transform = `translate3d(0, ${offset * speed}px, 0)`;
            });
        };

        updateParallax();
        window.addEventListener("scroll", updateParallax, { passive: true });
    }
});
