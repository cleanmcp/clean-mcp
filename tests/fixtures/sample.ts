// Sample TypeScript file for parser tests

interface User {
    id: number;
    name: string;
    email: string;
}

type UserRole = "admin" | "user" | "guest";

function createUser(name: string, email: string): User {
    return { id: Date.now(), name, email };
}

export const validateEmail = (email: string): boolean => {
    return email.includes("@");
};

export class AuthService {
    login(email: string, password: string): boolean {
        const isValid = validateEmail(email);
        return isValid;
    }
}
