# Platform SDK

`autograder_platform` is the only runtime protocol shared by B2, B3 and B4. Keep the protocol small: descriptor metadata, a host context, a router factory and a health report. Business models stay in the owning module.
