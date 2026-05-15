"""
Day 6: A hand-designed benchmark of 10 arguments.

Each test case has a KNOWN intended weakness. The eval harness runs
find_hole on each, then uses an LLM judge to check whether find_hole
correctly identified the same conceptual weakness.

Diversity is the whole point: 10 different fallacy categories so we can
spot whether find_hole has a blind spot for one specific type.

Note: each `intended_weakness` describes the *concept* the analysis should
catch, not the exact wording. The LLM judge will check conceptual match.
"""

BENCHMARK = [
    {
        "id": 1,
        "category": "absolute_claim",
        "argument": (
            "Test-driven development always prevents bugs in production code."
        ),
        "intended_weakness": (
            "The word 'always' and 'prevents' make this an absolute claim "
            "implying a 100% success rate. TDD reduces but does not eliminate "
            "bugs. The overreach is the load-bearing weakness."
        ),
    },
    {
        "id": 2,
        "category": "false_dichotomy",
        "argument": (
            "Either we adopt microservices for our backend or our system "
            "will never scale beyond a few thousand users."
        ),
        "intended_weakness": (
            "False dichotomy: the argument presents only two options "
            "(microservices vs. doesn't scale) while ignoring that monoliths, "
            "modular monoliths, and hybrid architectures can also scale."
        ),
    },
    {
        "id": 3,
        "category": "appeal_to_authority",
        "argument": (
            "Linus Torvalds said C++ is a terrible language, therefore C++ "
            "should not be used for new systems-level projects."
        ),
        "intended_weakness": (
            "Appeal to authority: Torvalds's opinion alone is not evidence "
            "about C++'s actual suitability for systems work. The conclusion "
            "rests entirely on who said it, not on technical merits."
        ),
    },
    {
        "id": 4,
        "category": "cherry_picking",
        "argument": (
            "A 2023 study at TechCorp showed remote workers were 13% more "
            "productive, which proves remote work is superior to office work."
        ),
        "intended_weakness": (
            "Cherry-picking: a single in-house study at one company is "
            "generalized to a universal claim about remote vs. office work, "
            "ignoring conflicting studies and selection biases."
        ),
    },
    {
        "id": 5,
        "category": "vague_terms",
        "argument": (
            "AI is going to fundamentally change everything about how we work."
        ),
        "intended_weakness": (
            "Vague terms: 'AI', 'fundamentally', 'everything', and 'how we "
            "work' are all undefined. The claim is unfalsifiable without "
            "concrete definitions and predictions."
        ),
    },
    {
        "id": 6,
        "category": "missing_evidence",
        "argument": (
            "Rust is the future of systems programming."
        ),
        "intended_weakness": (
            "Bare assertion: no evidence, no mechanism, no specifics about "
            "what 'future' means or what timeframe. The claim has no support "
            "at all."
        ),
    },
    {
        "id": 7,
        "category": "slippery_slope",
        "argument": (
            "If we let engineers pick their own tools, soon every team will "
            "use a different stack, then no one will be able to switch "
            "projects, then knowledge silos will destroy the company."
        ),
        "intended_weakness": (
            "Slippery slope: the chain from 'engineers pick tools' to "
            "'company destroyed' has no justification at each link. Each "
            "step is presented as inevitable when none are."
        ),
    },
    {
        "id": 8,
        "category": "hasty_generalization",
        "argument": (
            "I tried Kubernetes once on a side project and it was a nightmare "
            "to configure, so it's not worth using for any team."
        ),
        "intended_weakness": (
            "Hasty generalization: a single n=1 experience on a side project "
            "is used to draw a universal conclusion about all team contexts. "
            "Sample size and context-mismatch."
        ),
    },
    {
        "id": 9,
        "category": "burden_of_proof",
        "argument": (
            "There's no evidence that pair programming hurts productivity, "
            "so we should make all developers pair-program full time."
        ),
        "intended_weakness": (
            "Reverses the burden of proof: absence of negative evidence is "
            "not positive evidence. The argument presents 'no evidence "
            "against' as if it were 'evidence for'."
        ),
    },
    {
        "id": 10,
        "category": "strawman",
        "argument": (
            "Critics of static typing think types are useless and slow you "
            "down. But anyone who has worked on a large codebase knows types "
            "catch bugs. Therefore static typing is essential."
        ),
        "intended_weakness": (
            "Strawman: critics of static typing don't actually claim 'types "
            "are useless'; they argue about cost-benefit at different scales. "
            "The argument defeats a position no one holds."
        ),
    },
]
