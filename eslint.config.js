const js = require("@eslint/js");

module.exports = [
  {
    ignores: ["node_modules/", ".venv/", "eslint.config.js"],
  },
  js.configs.recommended,
  {
    files: ["app/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: {
        document: "readonly",
        setTimeout: "readonly",
        Promise: "readonly",
        HTMLTextAreaElement: "readonly",
        MouseEvent: "readonly",
        Event: "readonly",
        Math: "readonly",
        Object: "readonly",
        console: "readonly",
      },
    },
  },
];
