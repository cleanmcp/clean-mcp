// Sample JavaScript file for parser tests

function add(a, b) {
    return a + b;
}

const multiply = (a, b) => {
    return a * b;
};

class Calculator {
    constructor() {
        this.result = 0;
    }

    calculate(a, b) {
        this.result = add(a, b);
        return this.result;
    }
}

export function subtract(a, b) {
    return a - b;
}

export const divide = (a, b) => {
    if (b === 0) throw new Error("Division by zero");
    return a / b;
};
