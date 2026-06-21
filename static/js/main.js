document.addEventListener("DOMContentLoaded", () => {
    document.documentElement.classList.add("js-ready");

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

    const backToTopBtn = document.getElementById("backToTopBtn");
    if (backToTopBtn) {
        const toggleBackToTop = () => {
            if (window.scrollY > 400) {
                backToTopBtn.classList.add("visible");
            } else {
                backToTopBtn.classList.remove("visible");
            }
        };

        window.addEventListener("scroll", toggleBackToTop, { passive: true });
        toggleBackToTop();
    }

    const activatePortalTab = (trigger, targetId, shouldScroll = true) => {
        if (!targetId) {
            return false;
        }

        const target = document.querySelector(targetId);
        if (!target) {
            return false;
        }

        let tabScope = trigger.closest("[data-portal-tabs]");
        if (!tabScope || !tabScope.contains(target)) {
            tabScope = document;
        }
        if (target.classList.contains("portal-tab-panel")) {
            tabScope.querySelectorAll(".portal-tab-panel").forEach((panel) => {
                panel.classList.toggle("active", panel === target);
            });
            target.classList.add("visible");
            target.querySelectorAll(".reveal").forEach((element) => element.classList.add("visible"));

            tabScope.querySelectorAll("[data-tab-target]").forEach((button) => {
                const isActive = button.getAttribute("data-tab-target") === targetId;
                button.classList.toggle("active", isActive);
                button.setAttribute("aria-selected", String(isActive));
            });
        }

        if (shouldScroll) {
            const toolbar = tabScope.querySelector?.(".portal-toolbar") || target;
            toolbar.scrollIntoView({ behavior: "smooth", block: "start" });
        }

        return true;
    };

    document.querySelectorAll("[data-jump-select]").forEach((select) => {
        select.addEventListener("change", (event) => {
            const targetId = event.target.value;
            if (activatePortalTab(event.target, targetId)) {
                event.target.value = "";
            }
        });
    });

    document.querySelectorAll("[data-tab-target]").forEach((button) => {
        button.addEventListener("click", (event) => {
            activatePortalTab(event.currentTarget, event.currentTarget.getAttribute("data-tab-target"), false);
        });
    });

    const roleFilterStorage = {
        get(key) {
            try {
                return window.sessionStorage.getItem(key);
            } catch (error) {
                return null;
            }
        },
        set(key, value) {
            try {
                window.sessionStorage.setItem(key, value);
            } catch (error) {
                // Filtering still works when browser storage is unavailable.
            }
        },
    };

    const setActiveRoleFilter = (targetId, selectedRole, shouldStore = true) => {
        document.querySelectorAll(`[data-role-filter-target="${targetId}"]`).forEach((peer) => {
            const isActive = peer.getAttribute("data-role-filter") === selectedRole;
            peer.classList.toggle("active", isActive);
            peer.setAttribute("aria-pressed", String(isActive));
        });

        if (shouldStore) {
            roleFilterStorage.set(`role-filter:${targetId}`, selectedRole);
        }
    };

    const getFilterInputForTarget = (targetId) => {
        return Array.from(document.querySelectorAll("[data-filter-target], [data-admin-filter], [data-workspace-filter]")).find((input) => {
            return (
                input.getAttribute("data-filter-target") === targetId ||
                input.getAttribute("data-admin-filter") === targetId ||
                input.getAttribute("data-workspace-filter") === targetId
            );
        });
    };

    const applyCardFilters = (targetId) => {
        const target = document.querySelector(targetId);
        if (!target) {
            return;
        }

        const searchInput = getFilterInputForTarget(targetId);
        const query = searchInput ? searchInput.value.trim().toLowerCase() : "";
        const activeRoleButton = document.querySelector(`[data-role-filter-target="${targetId}"].active`);
        const activeRole = activeRoleButton ? activeRoleButton.getAttribute("data-role-filter") : "all";
        let visibleCount = 0;

        target.querySelectorAll("[data-filter-card]").forEach((card) => {
            const matchesQuery = !query || card.textContent.toLowerCase().includes(query);
            const cardRole = card.getAttribute("data-user-role");
            const matchesRole = !cardRole || activeRole === "all" || cardRole === activeRole;
            const shouldShow = matchesQuery && matchesRole;
            card.hidden = !shouldShow;
            if (shouldShow) {
                visibleCount += 1;
            }
        });

        document.querySelectorAll(`[data-filter-empty="${targetId}"]`).forEach((emptyState) => {
            emptyState.hidden = visibleCount > 0;
        });
    };

    document.querySelectorAll("[data-filter-target], [data-admin-filter], [data-workspace-filter]").forEach((input) => {
        const handleFilterInput = (event) => {
            const targetId =
                event.target.getAttribute("data-filter-target") ||
                event.target.getAttribute("data-admin-filter") ||
                event.target.getAttribute("data-workspace-filter");
            applyCardFilters(targetId);
        };

        input.addEventListener("input", handleFilterInput);
        input.addEventListener("search", handleFilterInput);
        input.addEventListener("change", handleFilterInput);
        input.addEventListener("keyup", handleFilterInput);
    });

    document.querySelectorAll("[data-role-filter-target]").forEach((button) => {
        const targetId = button.getAttribute("data-role-filter-target");
        const savedRole = roleFilterStorage.get(`role-filter:${targetId}`);

        if (savedRole) {
            setActiveRoleFilter(targetId, savedRole, false);
        }
    });

    document.addEventListener("click", (event) => {
        const currentButton = event.target.closest("[data-role-filter-target]");
        if (!currentButton) {
            return;
        }

        const targetId = currentButton.getAttribute("data-role-filter-target");
        const selectedRole = currentButton.getAttribute("data-role-filter");
        const activateTargetId = currentButton.getAttribute("data-role-filter-activate");
        if (activateTargetId) {
            activatePortalTab(currentButton, activateTargetId, false);
        }
        setActiveRoleFilter(targetId, selectedRole);
        applyCardFilters(targetId);
    });

    Array.from(new Set(Array.from(document.querySelectorAll("[data-role-filter-target]")).map((button) => button.getAttribute("data-role-filter-target")))).forEach(applyCardFilters);

    document.querySelectorAll("[data-confirm]").forEach((element) => {
        element.addEventListener("click", (event) => {
            const message = element.getAttribute("data-confirm");
            if (message && !window.confirm(message)) {
                event.preventDefault();
            }
        });
    });
});
